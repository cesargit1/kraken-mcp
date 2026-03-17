"""
bot.py — Main async trading loop.

Fast loop (every 5 min, no AI):
  - Fetch new candles
  - Compute indicators
  - Check thresholds + 60-min timer + cooldown
  - Trigger AI pipeline for flagged tickers

Event-triggered AI (per flagged ticker):
  - search_x() for social context
  - 3 specialist agents in parallel (technical, social, risk)
  - 1 decision agent synthesizing all three
  - Log to Supabase + execute via Kraken

Usage:
  python3 bot.py              # paper mode (default)
  TRADING_MODE=live python3 bot.py
"""

import asyncio
import json
import os
import urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv

from core import run_kraken, dispatch_tool, Mode, MODE
import db
import fetch as fetcher
import indicators as ind
from bot_state import update_ticker_state, update_cycle_state
from agents.technical import analyze as technical_analyze
from agents.social    import analyze as social_analyze
from agents.risk      import analyze as risk_analyze
from agents.decision  import analyze as decision_analyze

load_dotenv()

# Timing defaults — overridden at startup by DB settings (see fast_loop)
POLL_INTERVAL_SEC = 300
AI_TIMER_MIN      = 60
COOLDOWN_MIN      = 30

# Serialises entry execution across concurrently-running tickers so the
# max_open_positions + available_cash check is atomic (no TOCTOU race).
_entry_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Current price helper
# ---------------------------------------------------------------------------

def _fetch_price_http(pair: str) -> float | None:
    """Fallback: fetch price directly from Kraken public REST API.
    Used when kraken-cli binary is unavailable (e.g. Railway deployment)."""
    try:
        url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
        req = urllib.request.Request(url, headers={"User-Agent": "kraken-mcp/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if data.get("error"):
            return None
        for val in data.get("result", {}).values():
            if isinstance(val, dict) and "c" in val:
                return float(val["c"][0])
    except Exception:
        pass
    return None


def get_current_price(pair: str, asset_class: str) -> float | None:
    args = ["ticker", pair]
    # --asset-class only accepts tokenized_asset or forex; omit for spot/crypto
    if asset_class and asset_class not in ("spot", ""):
        args += ["--asset-class", asset_class]
    result = run_kraken(args)
    if "error" not in result:
        for key, val in result.items():
            if isinstance(val, dict) and "c" in val:
                try:
                    return float(val["c"][0])
                except (KeyError, IndexError, TypeError):
                    pass
    # CLI unavailable or returned an error — fall back to REST API
    return _fetch_price_http(pair)


# ---------------------------------------------------------------------------
# Stop-loss close (no AI — immediate execution)
# ---------------------------------------------------------------------------

async def close_position_stop_loss(
    ticker_row: dict, position: dict, current_price: float
) -> None:
    ticker      = ticker_row["ticker"]
    pair        = ticker_row.get("pair", ticker)
    asset_class = ticker_row.get("asset_class", "spot")
    side        = position["side"]
    volume      = position["quantity"]
    leverage    = position.get("leverage", 1)

    print(f"  [STOP-LOSS] {ticker} {side.upper()} @ ${current_price:.2f}  "
          f"(stop was ${position.get('stop_loss',0):.2f})")

    if MODE == Mode.PAPER and asset_class == "tokenized_asset":
        exec_result = {"simulated": True, "action": "close", "reason": "stop_loss",
                       "volume": volume, "price": current_price}
    else:
        # Long → sell; Short → buy to cover
        args = {"pair": pair, "volume": volume, "asset_class": asset_class,
                "order_type": "market"}
        if leverage > 1:
            args["leverage"] = leverage
            args["reduce_only"] = True
        tool = "sell" if side == "long" else "buy"
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, lambda: dispatch_tool(tool, args, MODE))
        exec_result = {"raw": raw, "volume": volume, "price": current_price}

    closed = db.close_position(ticker, current_price, "stop_loss")
    pnl = closed.get("realized_pnl", 0) if closed else 0
    print(f"  [STOP-LOSS] Closed {ticker}. P&L: ${pnl:.2f}")

    # Log to agent_log (action=sell/cover, trigger=stop_loss) so history is complete
    close_action = "sell" if side == "long" else "cover"
    agent_log_id = db.log_agent_run(
        ticker=ticker,
        action=close_action,
        technical={},
        social={},
        risk={},
        decision_reasoning=f"Stop-loss triggered. {side.upper()} position closed at ${current_price:.2f} (stop was ${position.get('stop_loss', 0):.2f}). P&L: ${pnl:.2f}",
        pair=ticker_row.get("pair"),
        trigger_flags="stop_loss",
        position_side=side,
        executed=True,
    )
    # Log the close transaction to ledger for financial audit trail
    _close_notional = round(volume * current_price, 2)
    db.log_transaction(
        agent_log_id=agent_log_id,
        ticker=ticker,
        action=close_action,
        current_price=current_price,
        volume=volume,
        notional_usd=_close_notional,
        leverage=leverage,
        stop_loss=None,
        fee=db.calc_trade_fee(_close_notional),
        pair=ticker_row.get("pair"),
        order_type="market",
        source_type="paper" if MODE == Mode.PAPER else "spot",
        is_simulated=exec_result.get("simulated", False),
        execution_result=exec_result,
    )


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

async def execute_trade(ticker_row: dict, decision: dict, current_price: float) -> dict:
    pair        = ticker_row.get("pair", ticker_row["ticker"])
    asset_class = ticker_row.get("asset_class", "spot")
    action      = decision["action"]
    size_usd    = decision.get("size_usd") or 0
    leverage    = decision.get("leverage", 1)

    volume = round(size_usd / current_price, 4) if current_price and size_usd else 0

    # For exit actions (cover/sell), use the open position's volume, not a new size
    if action in ("cover", "sell"):
        open_pos = db.get_open_position(ticker_row["ticker"])
        if open_pos:
            volume = open_pos["quantity"]
            leverage = open_pos.get("leverage", 1)

    if volume <= 0:
        return {"error": "zero volume computed"}

    # Kraken CLI paper mode only supports spot crypto pairs.
    # xStocks (tokenized_asset) are simulated internally in paper mode.
    if MODE == Mode.PAPER and asset_class == "tokenized_asset":
        result = {
            "simulated": True,
            "action":    action,
            "volume":    volume,
            "price":     current_price,
            "size_usd":  round(volume * current_price, 2),
            "leverage":  leverage,
        }
        print(f"  [sim]  {action.upper()} {volume} {pair} @ ~${current_price:.2f}  (paper sim — xStock not supported by Kraken paper CLI)")
        return result

    # "short" = opening a margin sell; requires leverage >= 2
    if action == "short" and leverage < 2:
        leverage = 2

    args = {
        "pair":        pair,
        "volume":      volume,
        "asset_class": asset_class,
        "order_type":  "market",
    }
    if leverage > 1:
        args["leverage"] = leverage
    # reduce_only for closing existing margin positions (prevents opening new opposite side)
    if action in ("sell", "cover") and leverage > 1:
        args["reduce_only"] = True

    loop = asyncio.get_event_loop()

    if action == "buy":
        raw = await loop.run_in_executor(None, lambda: dispatch_tool("buy", args, MODE))
    elif action in ("sell", "short"):
        raw = await loop.run_in_executor(None, lambda: dispatch_tool("sell", args, MODE))
    elif action == "cover":
        # Cover a short = buy back the shares
        raw = await loop.run_in_executor(None, lambda: dispatch_tool("buy", args, MODE))
    else:
        return {"skipped": "hold"}

    print(f"  [exec] {action.upper()} {volume} {pair} @ ~${current_price:.2f}: {raw[:120]}")
    return {"raw": raw, "volume": volume, "price": current_price}


# ---------------------------------------------------------------------------
# Per-ticker AI pipeline
# ---------------------------------------------------------------------------

async def process_ticker(ticker_row: dict, flags: list[str], all_indicators: dict, timer_driven: bool = False) -> None:
    ticker      = ticker_row["ticker"]
    pair        = ticker_row.get("pair", ticker)
    asset_class = ticker_row.get("asset_class", "spot")

    trigger_label = ",".join(flags) if flags else ""
    if timer_driven:
        trigger_label = (trigger_label + ",timer") if trigger_label else "timer"

    print(f"\n  [AI ▶] {ticker}  trigger={trigger_label}")
    # Stamp last_ai_run + set flag cooldowns immediately — prevents re-triggering
    # if the next poll cycle starts while this slow LLM pipeline is still running.
    db.stamp_ai_run_started(ticker)
    for flag in flags:
        db.set_cooldown(ticker, flag)
    update_ticker_state(ticker, stage="context_fetch")

    balance_cmd = ["paper", "balance"] if MODE == Mode.PAPER else ["balance"]

    # Fetch non-social context in parallel — X data is fetched by the social agent itself
    loop = asyncio.get_event_loop()
    current_price, open_position, holdings, settings = await asyncio.gather(
        loop.run_in_executor(None, lambda: get_current_price(pair, asset_class)),
        loop.run_in_executor(None, lambda: db.get_open_position(ticker)),
        loop.run_in_executor(None, lambda: run_kraken(balance_cmd)),
        loop.run_in_executor(None, db.get_settings),
    )

    # Build per-specialist context payloads (each agent sees only what it needs)
    technical_context = {
        "ticker":        ticker,
        "current_price": current_price,
        "indicators":    all_indicators,
        "flags":         flags,
    }
    social_context = {
        "ticker_row": ticker_row,
        "obv":        {tf: v.get("obv") for tf, v in all_indicators.items()},
    }
    risk_context = {
        "ticker":        ticker,
        "current_price": current_price,
        "atr":           {tf: v.get("atr") for tf, v in all_indicators.items()},
        "holdings":      holdings,
        "flags":         flags,
        "settings":      settings,
    }

    # Run 3 specialists in parallel (~3 sec wall time)
    print(f"  [AI]  Running 3 specialists in parallel...")
    update_ticker_state(ticker, stage="specialists")
    technical, social, risk = await asyncio.gather(
        technical_analyze(technical_context),
        social_analyze(social_context),
        risk_analyze(risk_context),
    )

    print(f"  [AI]  Technical : {technical.get('signal','?')} ({technical.get('confidence','?')}%) — {technical.get('pattern','')}")
    print(f"  [AI]  Social    : {social.get('signal','?')} ({social.get('confidence','?')}%) — hype={social.get('hype_vs_real','?')}")
    print(f"  [AI]  Risk      : max ${risk.get('max_position_usd','?')} lev={risk.get('recommended_leverage','?')}x")

    # Guard: abort pipeline if any specialist returned a parse/API error
    failed = []
    for name, result in [("technical", technical), ("social", social), ("risk", risk)]:
        if isinstance(result, dict) and "error" in result:
            failed.append(f"{name}: {result['error']}")
    if failed:
        print(f"  [ABORT] {ticker}: specialist errors — {'; '.join(failed)}")
        update_ticker_state(ticker, status="error", error=f"specialist_failure: {'; '.join(failed)}")
        db.update_signal_state(ticker, {"action": "hold"}, trigger_label)
        return

    # Store specialist results in state for dashboard
    update_ticker_state(ticker,
        specialist_technical=technical,
        specialist_social=social,
        specialist_risk=risk,
    )

    # Enrich open_position with computed P&L + time-in-trade so the decision agent
    # doesn't have to do arithmetic from raw prices.
    enriched_position = None
    if open_position and current_price:
        ep          = open_position.get("entry_price") or 0
        side        = open_position.get("side", "long")
        qty         = open_position.get("quantity") or 0
        raw_pct     = ((current_price - ep) / ep * 100) if ep else 0
        signed_pct  = raw_pct if side == "long" else -raw_pct
        signed_usd  = round(signed_pct / 100 * ep * qty, 2)

        opened_at_str = open_position.get("opened_at")
        hrs_open = None
        if opened_at_str:
            try:
                from datetime import timezone as _tz
                opened_dt = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                hrs_open  = round((datetime.now(_tz.utc) - opened_dt).total_seconds() / 3600, 1)
            except Exception:
                pass

        enriched_position = {
            **open_position,
            "unrealized_pnl_pct": round(signed_pct, 2),
            "unrealized_pnl_usd": signed_usd,
            "time_in_trade_hrs":  hrs_open,
        }

    # Decision agent
    decision_context = {
        "ticker":              ticker,
        "current_price":       current_price,
        "open_position":       enriched_position,   # None if flat, else {side, quantity, entry_price, stop_loss, leverage, unrealized_pnl_pct, unrealized_pnl_usd, time_in_trade_hrs}
        "current_holdings":    holdings,
        "technical_analysis":  technical,
        "social_analysis":     social,
        "risk_analysis":       risk,
    }
    print(f"  [AI]  Running decision agent...")
    update_ticker_state(ticker, stage="decision",
                        technical_signal=technical.get("signal"), social_signal=social.get("signal"))
    decision = await decision_analyze(decision_context)

    action = decision.get("action", "hold")

    # Hard guard: enforce position state rules regardless of what the AI returned.
    # Prevents stacking duplicate entries if the AI ignores its prompt rules.
    if open_position:
        side = open_position.get("side")
        if side == "short" and action not in ("hold", "cover"):
            print(f"  [GUARD] {ticker}: already short — overriding AI action '{action}' → hold")
            action = "hold"
        elif side == "long" and action not in ("hold", "sell"):
            print(f"  [GUARD] {ticker}: already long — overriding AI action '{action}' → hold")
            action = "hold"
    else:
        # Flat — block exit actions that make no sense without a position
        if action in ("sell", "cover"):
            print(f"  [GUARD] {ticker}: no open position — overriding AI action '{action}' → hold")
            action = "hold"

    print(f"  [AI ◀] {ticker} → {action.upper()} (confidence={decision.get('confidence','?')}%)")
    print(f"         {decision.get('reasoning','')[:140]}")

    size_usd = decision.get("size_usd") or 0

    # Log AI decision session to Supabase (no financials — those go to transaction_ledger)
    agent_log_id = db.log_agent_run(
        ticker=ticker,
        action=action,
        technical=technical,
        social=social,
        risk=risk,
        decision_reasoning=decision.get("reasoning", ""),
        decision_json=decision,
        pair=ticker_row.get("pair"),
        trigger_flags=trigger_label,
        position_side=open_position.get("side") if open_position else "flat",
        indicators_snapshot=all_indicators,
        executed=False,
    )

    # Execute if not hold
    is_entry = action in ("buy", "short")
    is_exit  = action in ("sell", "cover")
    if action == "hold":
        pass  # nothing to do
    elif is_entry and not decision.get("size_usd"):
        print(f"  [SKIP] {ticker}: action={action} but AI returned null size_usd — not executing. Check decision agent prompt.")
    elif not current_price:
        print(f"  [SKIP] {ticker}: no current price — not executing.")
    else:
        # ── Enforce settings hard limits before execution ─────────────────
        if action in ("buy", "short"):
            # Acquire entry lock to prevent concurrent tickers from both passing
            # max_open_positions + cash checks before either executes (TOCTOU race).
            async with _entry_lock:
                # -- Max open positions guard --
                open_positions = db.get_all_open_positions()
                if len(open_positions) >= settings.get("max_open_positions", 10):
                    print(f"  [LIMIT] {ticker}: max_open_positions ({settings['max_open_positions']}) reached. Skipping.")
                    db.update_signal_state(ticker, decision, trigger_label)
                    for flag in flags:
                        db.set_cooldown(ticker, flag)
                    return

                # -- Clamp size to max_position limits --
                paper_capital = settings.get("paper_capital", 1000.0)
                max_pct_usd   = paper_capital * settings.get("max_position_pct", 20) / 100
                max_hard_usd  = settings.get("max_position_usd") or max_pct_usd
                size_cap      = min(max_pct_usd, max_hard_usd)
                if (decision.get("size_usd") or 0) > size_cap:
                    print(f"  [LIMIT] {ticker}: clamping size ${decision['size_usd']:,.0f} → ${size_cap:,.0f} (max_position)")
                    decision["size_usd"] = round(size_cap, 2)

                # -- Clamp leverage to max_leverage --
                max_lev = settings.get("max_leverage", 3)
                if (decision.get("leverage") or 1) > max_lev:
                    print(f"  [LIMIT] {ticker}: clamping leverage {decision['leverage']}x → {max_lev}x")
                    decision["leverage"] = max_lev

                # -- Cash check: block if not enough margin available --
                used_margin    = sum(
                    ((p["entry_price"] or 0) * (p["quantity"] or 0)) / max(p["leverage"] or 1, 1)
                    for p in open_positions
                )
                realized       = db.get_realized_pnl()
                available_cash = paper_capital - used_margin + realized
                required_margin = (decision.get("size_usd") or 0) / max(decision.get("leverage", 1), 1)
                if required_margin > available_cash:
                    print(f"  [CASH] {ticker}: insufficient cash — need ${required_margin:,.0f}, have ${available_cash:,.0f}. Skipping.")
                    db.update_signal_state(ticker, decision, trigger_label)
                    for flag in flags:
                        db.set_cooldown(ticker, flag)
                    return

                result = await execute_trade(ticker_row, decision, current_price)
                # Record the exchange transaction receipt in transaction_ledger
                exec_volume = result.get("volume") or round(size_usd / (current_price or 1), 4)
                _exec_notional = round(exec_volume * (current_price or 0), 2)
                db.log_transaction(
                    agent_log_id=agent_log_id,
                    ticker=ticker,
                    action=action,
                    current_price=current_price or 0,
                    volume=exec_volume,
                    notional_usd=_exec_notional,
                    leverage=decision.get("leverage", 1),
                    stop_loss=decision.get("stop_loss"),
                    fee=db.calc_trade_fee(_exec_notional),
                    pair=ticker_row.get("pair"),
                    order_type="market",
                    source_type="paper" if MODE == Mode.PAPER else "spot",
                    is_simulated=result.get("simulated", False),
                    execution_result=result,
                )
                db.mark_agent_run_executed(agent_log_id)
                if not result.get("error"):
                    stop_price = decision.get("stop_loss")
                    volume = exec_volume
                    db.open_position(
                        ticker=ticker,
                        side="long" if action == "buy" else "short",
                        quantity=volume,
                        entry_price=current_price,
                        stop_loss=stop_price,
                        leverage=decision.get("leverage", 1),
                        agent_log_id=agent_log_id,
                    )
        else:
            # Exit actions (sell/cover) — no lock needed, exits are safe to run concurrently
            result = await execute_trade(ticker_row, decision, current_price)
            exec_volume = result.get("volume") or round(size_usd / (current_price or 1), 4)
            _exec_notional = round(exec_volume * (current_price or 0), 2)
            db.log_transaction(
                agent_log_id=agent_log_id,
                ticker=ticker,
                action=action,
                current_price=current_price or 0,
                volume=exec_volume,
                notional_usd=_exec_notional,
                leverage=decision.get("leverage", 1),
                stop_loss=decision.get("stop_loss"),
                fee=db.calc_trade_fee(_exec_notional),
                pair=ticker_row.get("pair"),
                order_type="market",
                source_type="paper" if MODE == Mode.PAPER else "spot",
                is_simulated=result.get("simulated", False),
                execution_result=result,
            )
            db.mark_agent_run_executed(agent_log_id)
            if not result.get("error"):
                db.close_position(ticker, current_price, "ai_signal")

    # Persist signal state + set per-event cooldowns
    db.update_signal_state(ticker, decision, trigger_label)
    for flag in flags:
        db.set_cooldown(ticker, flag)
    update_ticker_state(ticker, status="done", stage="complete",
                        last_action=action,
                        last_confidence=decision.get("confidence"),
                        last_reasoning=decision.get("reasoning", "")[:500],
                        decision_size_usd=decision.get("size_usd"),
                        decision_leverage=decision.get("leverage"),
                        decision_stop_loss=decision.get("stop_loss"))


# ---------------------------------------------------------------------------
# Market hours helper
# ---------------------------------------------------------------------------

def is_market_open(asset_class: str) -> bool:
    """Return True if the relevant market is open.
    Crypto (spot) trades 24/7.  Tokenized assets follow NYSE hours Mon-Fri 09:30-16:00 ET."""
    if asset_class in ("spot", "", None):
        return True
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now_et <= market_close


# ---------------------------------------------------------------------------
# Per-ticker fast-loop worker (runs concurrently for all watchlist tickers)
# ---------------------------------------------------------------------------

async def _process_one_ticker(ticker_row: dict) -> None:
    """Per-ticker work for one fast-loop cycle: candles → indicators → stop-loss → AI trigger."""
    ticker = ticker_row["ticker"]
    loop   = asyncio.get_event_loop()
    import time as _time
    update_ticker_state(ticker, status="running", stage="candles", flags=[], started_at=_time.time())
    try:
        # 1. Fetch new candles (non-blocking so other tickers run concurrently)
        await loop.run_in_executor(None, lambda: fetcher.update_candles(ticker_row))

        # 2. Compute indicators at all 4 timeframes (CPU-bound, fast)
        update_ticker_state(ticker, stage="indicators")
        all_indicators = ind.compute_all_timeframes(ticker_row)

        if not all_indicators:
            print(f"  [skip] {ticker}: not enough candle data yet")
            update_ticker_state(ticker, status="skipped", stage="no_data", skip_reason="not enough candle data")
            return

        # Snapshot indicator values + last-candle OHLCV per timeframe for the dashboard
        _ind_snap = {}
        for tf, v in all_indicators.items():
            _ind_snap[tf] = {
                "open":        v.get("latest_open"),
                "high":        v.get("latest_high"),
                "low":         v.get("latest_low"),
                "close":       v.get("latest_close"),
                "volume":      v.get("latest_volume"),
                "rsi":         v.get("rsi"),
                "macd":        v.get("macd"),
                "macd_signal": v.get("macd_signal"),
                "macd_hist":   v.get("macd_hist"),
                "bb_upper":    v.get("bb_upper"),
                "bb_middle":   v.get("bb_middle"),
                "bb_lower":    v.get("bb_lower"),
                "ema_20":      v.get("ema_20"),
                "ema_50":      v.get("ema_50"),
                "obv":         v.get("obv"),
                "atr":         v.get("atr"),
                "flags":       v.get("threshold_flags", []),
            }
        update_ticker_state(ticker, indicators_snap=_ind_snap)

        # 3. Fetch current price + open position in parallel for stop-loss check
        update_ticker_state(ticker, stage="price_check")
        pair        = ticker_row.get("pair", ticker)
        asset_class = ticker_row.get("asset_class", "spot")
        current_price, open_pos = await asyncio.gather(
            loop.run_in_executor(None, lambda: get_current_price(pair, asset_class)),
            loop.run_in_executor(None, lambda: db.get_open_position(ticker)),
        )
        update_ticker_state(ticker, open_position=open_pos)

        # 4. Stop-loss check + trailing stop update (no AI — immediate / mechanical)
        if open_pos and current_price:
            sl   = open_pos.get("stop_loss")
            side = open_pos.get("side")

            # --- Trailing stop: ratchet stop-loss toward price using ATR ---
            # Prefer 4h ATR for tokenized assets (daily instruments; 1h is too noisy).
            # Fall back to 1h ATR for spot crypto where 1h is the primary timeframe.
            atr_4h = (all_indicators.get("4h") or {}).get("atr")
            atr_1h = (all_indicators.get("1h") or {}).get("atr")
            trail_atr = (atr_4h if asset_class == "tokenized_asset" else None) or atr_1h
            if trail_atr and sl:
                hw = open_pos.get("high_water_price") or open_pos.get("entry_price", 0)
                settings = await loop.run_in_executor(None, db.get_settings)
                atr_mult = settings.get("trailing_stop_atr_mult", 2.0)

                if side == "long" and current_price > hw:
                    new_stop = current_price - atr_mult * trail_atr
                    if new_stop > sl:
                        print(f"  [TRAIL] {ticker} LONG: stop ${sl:.2f} → ${new_stop:.2f}  (hw ${hw:.2f} → ${current_price:.2f})")
                        await loop.run_in_executor(None, lambda: db.update_trailing_stop(ticker, current_price, new_stop))
                        sl = new_stop  # use updated stop for breach check below
                    else:
                        # New high-water but stop didn't tighten (ATR too wide) — still record the high
                        await loop.run_in_executor(None, lambda: db.update_trailing_stop(ticker, current_price, sl))

                elif side == "short" and current_price < hw:
                    new_stop = current_price + atr_mult * trail_atr
                    if new_stop < sl:
                        print(f"  [TRAIL] {ticker} SHORT: stop ${sl:.2f} → ${new_stop:.2f}  (hw ${hw:.2f} → ${current_price:.2f})")
                        await loop.run_in_executor(None, lambda: db.update_trailing_stop(ticker, current_price, new_stop))
                        sl = new_stop
                    else:
                        await loop.run_in_executor(None, lambda: db.update_trailing_stop(ticker, current_price, sl))

            # --- Breach check (uses updated trailing stop if it was ratcheted) ---
            if sl:
                breached = (
                    (side == "long"  and current_price <= sl) or
                    (side == "short" and current_price >= sl)
                )
                if breached:
                    update_ticker_state(ticker, stage="stop_loss", current_price=current_price)
                    await close_position_stop_loss(ticker_row, open_pos, current_price)

        # 5. Market hours gate — skip AI for tokenized assets when exchange is closed
        if not is_market_open(asset_class):
            print(f"  [skip] {ticker}: market closed for {asset_class} — skipping AI pipeline")
            update_ticker_state(ticker, status="skipped", stage="complete", skip_reason="market closed")
            return

        # 6. Collect all threshold flags
        flags = ind.any_flags(all_indicators)
        update_ticker_state(ticker, stage="flag_check", flags=flags, current_price=current_price)

        # 7. Fetch last AI run + all cooldown states in parallel
        results = await asyncio.gather(
            loop.run_in_executor(None, lambda: db.get_last_ai_run(ticker)),
            *[loop.run_in_executor(None, lambda f=f: db.check_cooldown(ticker, f, COOLDOWN_MIN))
              for f in flags],
        )
        last_ai       = results[0]
        cooldown_hits = results[1:]

        timer_expired = (
            last_ai is None
            or (datetime.now(timezone.utc) - last_ai).total_seconds() >= AI_TIMER_MIN * 60
        )
        active_flags = [f for f, on_cd in zip(flags, cooldown_hits) if not on_cd]

        # 8. Trigger AI if there are new flags or timer expired
        if active_flags or timer_expired:
            update_ticker_state(ticker, stage="ai_pipeline", active_flags=active_flags, timer_expired=timer_expired)
            await process_ticker(ticker_row, active_flags, all_indicators, timer_driven=timer_expired)
        else:
            skip_msg = f"no new flags, not yet due (last AI: {last_ai.strftime('%H:%M') if last_ai else 'never'})"
            print(f"  [skip] {ticker}: {skip_msg}")
            update_ticker_state(ticker, status="skipped", stage="complete", skip_reason=skip_msg)

    except Exception as e:
        import traceback
        print(f"  [error] {ticker}: {e}")
        traceback.print_exc()
        update_ticker_state(ticker, status="error", error=str(e))


# ---------------------------------------------------------------------------
# Fast loop
# ---------------------------------------------------------------------------

async def fast_loop() -> None:
    global POLL_INTERVAL_SEC, AI_TIMER_MIN, COOLDOWN_MIN

    # Load timing settings from DB at startup
    settings = db.get_settings()
    POLL_INTERVAL_SEC = int(settings.get("poll_interval_sec", 300))
    AI_TIMER_MIN      = int(settings.get("ai_timer_min",      60))
    COOLDOWN_MIN      = int(settings.get("cooldown_min",      30))

    update_cycle_state(poll_interval_sec=POLL_INTERVAL_SEC)

    mode_label = "LIVE" if MODE == Mode.LIVE else "paper"
    print(f"[bot] Starting — mode={mode_label}  poll={POLL_INTERVAL_SEC}s  ai_timer={AI_TIMER_MIN}min  cooldown={COOLDOWN_MIN}min")

    while True:
        import time as _time
        _cycle_start_ts = _time.time()
        update_cycle_state(
            last_cycle_at=_cycle_start_ts,
            next_cycle_at=_cycle_start_ts + POLL_INTERVAL_SEC,
        )
        cycle_start = datetime.now(timezone.utc)
        print(f"\n[bot] ── Cycle {cycle_start.strftime('%Y-%m-%d %H:%M:%S UTC')} ──")

        try:
            watchlist = db.get_watchlist()
            if not watchlist:
                print("[bot] Watchlist is empty. Add tickers to the watchlist table in Supabase.")
        except Exception as e:
            print(f"[bot] DB error getting watchlist: {e}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
            continue

        # Process all tickers concurrently — each runs its full pipeline in parallel
        await asyncio.gather(*[_process_one_ticker(row) for row in watchlist])

        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        sleep_for = max(0, POLL_INTERVAL_SEC - elapsed)
        print(f"\n[bot] Cycle complete in {elapsed:.1f}s — sleeping {sleep_for:.0f}s")
        update_cycle_state(next_cycle_at=_time.time() + sleep_for)
        await asyncio.sleep(sleep_for)


if __name__ == "__main__":
    asyncio.run(fast_loop())
