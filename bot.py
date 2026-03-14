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
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from core import run_kraken, search_x, dispatch_tool, Mode, MODE
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


# ---------------------------------------------------------------------------
# Current price helper
# ---------------------------------------------------------------------------

def get_current_price(pair: str, asset_class: str) -> float | None:
    result = run_kraken(["ticker", pair, "--asset-class", asset_class])
    for key, val in result.items():
        if isinstance(val, dict) and "c" in val:
            try:
                return float(val["c"][0])
            except (KeyError, IndexError, TypeError):
                pass
    return None


# ---------------------------------------------------------------------------
# Stop-loss close (no AI — immediate execution)
# ---------------------------------------------------------------------------

async def close_position_stop_loss(
    ticker_row: dict, position: dict, current_price: float
) -> None:
    ticker      = ticker_row["ticker"]
    pair        = ticker_row.get("pair", ticker)
    asset_class = ticker_row.get("asset_class", "tokenized_asset")
    side        = position["side"]
    volume      = position["volume"]
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
    pnl = closed.get("pnl", 0) if closed else 0
    print(f"  [STOP-LOSS] Closed {ticker}. P&L: ${pnl:.2f}")


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

async def execute_trade(ticker_row: dict, decision: dict, current_price: float) -> dict:
    pair        = ticker_row.get("pair", ticker_row["ticker"])
    asset_class = ticker_row.get("asset_class", "tokenized_asset")
    action      = decision["action"]
    size_usd    = decision.get("size_usd") or 0
    leverage    = decision.get("leverage", 1)

    volume = round(size_usd / current_price, 4) if current_price and size_usd else 0

    # For exit actions (cover/sell), use the open position's volume, not a new size
    if action in ("cover", "sell"):
        open_pos = db.get_open_position(ticker_row["ticker"])
        if open_pos:
            volume = open_pos["volume"]
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
    # reduce_only only for closing an existing long (plain "sell", no leverage)
    if action == "sell" and leverage <= 1:
        args["reduce_only"] = False  # closing long spot

    loop = asyncio.get_event_loop()

    if action == "buy":
        raw = await loop.run_in_executor(None, lambda: dispatch_tool("buy", args, MODE))
    elif action in ("sell", "short", "cover"):
        raw = await loop.run_in_executor(None, lambda: dispatch_tool("sell", args, MODE))
    else:
        return {"skipped": "hold"}

    print(f"  [exec] {action.upper()} {volume} {pair} @ ~${current_price:.2f}: {raw[:120]}")
    return {"raw": raw, "volume": volume, "price": current_price}


# ---------------------------------------------------------------------------
# Per-ticker AI pipeline
# ---------------------------------------------------------------------------

async def process_ticker(ticker_row: dict, flags: list[str], all_indicators: dict) -> None:
    ticker      = ticker_row["ticker"]
    pair        = ticker_row.get("pair", ticker)
    asset_class = ticker_row.get("asset_class", "tokenized_asset")

    print(f"\n  [AI ▶] {ticker}  flags={flags or ['timer']}")
    update_ticker_state(ticker, stage="context_fetch")

    # Prepare query strings before parallel fetch
    clean_ticker = ticker.replace("x", "")  # "NVDAx" → "NVDA"
    x_query = (
        f"Search X for posts about ${clean_ticker} stock ticker in the last hour. "
        "Include post texts, volume trends, and any notable news or sentiment."
    )
    balance_cmd = ["paper", "balance"] if MODE == Mode.PAPER else ["balance"]

    # Fetch all context in parallel (~3-5s wall time instead of ~15s sequential)
    loop = asyncio.get_event_loop()
    current_price, open_position, x_data, holdings, settings = await asyncio.gather(
        loop.run_in_executor(None, lambda: get_current_price(pair, asset_class)),
        loop.run_in_executor(None, lambda: db.get_open_position(ticker)),
        loop.run_in_executor(None, lambda: search_x(x_query)),
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
        "ticker":  ticker,
        "x_posts": x_data,
        "obv":     {tf: v.get("obv") for tf, v in all_indicators.items()},
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

    # Store specialist results in state for dashboard
    update_ticker_state(ticker,
        specialist_technical=technical,
        specialist_social=social,
        specialist_risk=risk,
    )

    # Decision agent
    decision_context = {
        "ticker":              ticker,
        "current_price":       current_price,
        "open_position":       open_position,   # None if flat, else {side, volume, entry_price, stop_loss, leverage}
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

    # Log full analysis chain to Supabase
    trade_id = db.log_trade(
        ticker=ticker,
        action=action,
        size=decision.get("size_usd") or 0,
        leverage=decision.get("leverage", 1),
        stop_loss=decision.get("stop_loss") or 0,
        entry_price=current_price or 0,
        technical=technical,
        social=social,
        risk=risk,
        decision_reasoning=decision.get("reasoning", ""),
        executed=False,
    )

    # Execute if not hold
    if action != "hold" and current_price and decision.get("size_usd"):
        # ── Enforce settings hard limits before execution ─────────────────
        if action in ("buy", "short"):
            # -- Max open positions guard --
            open_positions = db.get_all_open_positions()
            if len(open_positions) >= settings.get("max_open_positions", 10):
                print(f"  [LIMIT] {ticker}: max_open_positions ({settings['max_open_positions']}) reached. Skipping.")
                db.update_signal_state(ticker, decision, ",".join(flags) if flags else "timer")
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
                ((p["entry_price"] or 0) * (p["volume"] or 0)) / max(p["leverage"] or 1, 1)
                for p in open_positions
            )
            realized       = db.get_realized_pnl()
            available_cash = paper_capital - used_margin + realized
            required_margin = (decision.get("size_usd") or 0) / max(decision.get("leverage", 1), 1)
            if required_margin > available_cash:
                print(f"  [CASH] {ticker}: insufficient cash — need ${required_margin:,.0f}, have ${available_cash:,.0f}. Skipping.")
                db.update_signal_state(ticker, decision, ",".join(flags) if flags else "timer")
                for flag in flags:
                    db.set_cooldown(ticker, flag)
                return
        result = await execute_trade(ticker_row, decision, current_price)
        db.mark_trade_executed(trade_id, result)
        # Record open position (buy = long, short = short)
        if action in ("buy", "short") and not result.get("error"):
            stop_price = decision.get("stop_loss")
            volume = result.get("volume") or round(
                (decision.get("size_usd") or 0) / (current_price or 1), 4
            )
            db.open_position(
                ticker=ticker,
                side="long" if action == "buy" else "short",
                volume=volume,
                entry_price=current_price,
                stop_loss=stop_price,
                leverage=decision.get("leverage", 1),
                trade_log_id=trade_id,
            )
        # Close position on sell/cover
        elif action in ("sell", "cover") and not result.get("error"):
            db.close_position(ticker, current_price, "ai_signal")

    # Persist signal state + set per-event cooldowns
    db.update_signal_state(ticker, decision, ",".join(flags) if flags else "timer")
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
        asset_class = ticker_row.get("asset_class", "tokenized_asset")
        current_price, open_pos = await asyncio.gather(
            loop.run_in_executor(None, lambda: get_current_price(pair, asset_class)),
            loop.run_in_executor(None, lambda: db.get_open_position(ticker)),
        )
        update_ticker_state(ticker, open_position=open_pos)

        # 4. Stop-loss check (no AI — immediate close if breached)
        if open_pos and current_price:
            sl   = open_pos.get("stop_loss")
            side = open_pos.get("side")
            if sl:
                breached = (
                    (side == "long"  and current_price <= sl) or
                    (side == "short" and current_price >= sl)
                )
                if breached:
                    update_ticker_state(ticker, stage="stop_loss", current_price=current_price)
                    await close_position_stop_loss(ticker_row, open_pos, current_price)

        # 5. Collect all threshold flags
        flags = ind.any_flags(all_indicators)
        update_ticker_state(ticker, stage="flag_check", flags=flags, current_price=current_price)

        # 6. Fetch last AI run + all cooldown states in parallel
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

        # 7. Trigger AI if there are new flags or timer expired
        if active_flags or timer_expired:
            update_ticker_state(ticker, stage="ai_pipeline", active_flags=active_flags, timer_expired=timer_expired)
            await process_ticker(ticker_row, active_flags, all_indicators)
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
