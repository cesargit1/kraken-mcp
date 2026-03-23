"""
bot.py — Main async trading loop (paper trading only).

Fast loop (configurable interval, default 5 min, no AI):
  - Fetch new candles via Yahoo Finance
  - Compute indicators
  - Check thresholds + timer + cooldown
  - Trigger AI pipeline for flagged tickers

Event-triggered AI (per flagged ticker):
  - Manually orchestrated multi-agent pipeline:
    technical, social, risk agents run in parallel,
    then a decision agent synthesises all three outputs.
  - Log to Supabase + simulate trade in DB

All trades are paper-simulated (DB-tracked only). No real orders are ever submitted.

Usage:
  python3 bot.py
"""

import asyncio
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

import db
import fetch as fetcher
import indicators as ind
from bot_state import update_ticker_state, update_cycle_state
from core import run_orchestrated_decision, build_x_query

load_dotenv()

# Timing defaults — overridden at startup by DB settings (see fast_loop)
POLL_INTERVAL_SEC = 300
AI_TIMER_MIN      = 60
COOLDOWN_MIN      = 30


# ---------------------------------------------------------------------------
# Current price helper
# ---------------------------------------------------------------------------

def get_current_price(ticker: str) -> float | None:
    """Get current price via Yahoo Finance, fallback to latest DB candle close."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        price = getattr(info, "last_price", None)
        if price and float(price) > 0:
            return float(price)
        hist = yf.Ticker(ticker).history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        print(f"  [price] yfinance error for {ticker}: {e}")
    # Last resort: use the most recent candle close from DB
    for tf in ("1h", "4h", "1d"):
        candles = db.get_candle_window(ticker, tf, limit=1)
        if candles and candles[-1].get("close"):
            print(f"  [price] {ticker}: using latest {tf} candle close as price")
            return float(candles[-1]["close"])
    return None


# ---------------------------------------------------------------------------
# Stop-loss close (no AI — immediate execution)
# ---------------------------------------------------------------------------

async def close_position_stop_loss(
    ticker_row: dict, position: dict, current_price: float
) -> None:
    ticker   = ticker_row["ticker"]
    side     = position["side"]
    volume   = position["quantity"]
    leverage = position.get("leverage", 1)

    print(f"  [STOP-LOSS] {ticker} {side.upper()} @ ${current_price:.2f}  "
          f"(stop was ${position.get('stop_loss',0):.2f})")

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
        pair=ticker,
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
        fee=db.calc_trade_fee(_close_notional),
        realized_pnl=pnl,
    )


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

async def execute_trade(ticker_row: dict, decision: dict, current_price: float) -> dict:
    ticker   = ticker_row["ticker"]
    action   = decision["action"]
    size_usd = decision.get("size_usd") or 0
    leverage = decision.get("leverage") or 1

    volume = round(size_usd / current_price, 4) if current_price and size_usd else 0

    # For exit actions (cover/sell), use the open position's volume, not a new size
    if action in ("cover", "sell"):
        open_pos = db.get_open_position(ticker)
        if open_pos:
            volume = open_pos["quantity"]
            leverage = open_pos.get("leverage", 1)

    if volume <= 0:
        return {"error": "zero volume computed"}

    # Hard guards: block entries that violate position/size/leverage/cash limits
    if action in ("buy", "short"):
        settings = db.get_settings()
        paper_capital = settings.get("paper_capital", 1000.0)
        all_open = db.get_all_open_positions()

        # Guard: clamp size_usd to operator limits
        max_pct_usd  = paper_capital * settings.get("max_position_pct", 20) / 100
        max_hard_usd = settings.get("max_position_usd") or max_pct_usd
        size_cap     = min(max_pct_usd, max_hard_usd)
        if size_usd > size_cap:
            size_usd = round(size_cap, 2)
            volume = round(size_usd / current_price, 4) if current_price else 0

        # Guard: clamp leverage to operator limit
        max_lev = settings.get("max_leverage", 3)
        if leverage > max_lev:
            leverage = max_lev

        # Guard: insufficient cash
        used_margin = sum(
            ((p.get("entry_price") or 0) * (p.get("quantity") or 0)) / max(p.get("leverage") or 1, 1)
            for p in all_open
        )
        realized = db.get_realized_pnl()
        available_cash = paper_capital - used_margin + realized
        required_margin = size_usd / max(leverage, 1)
        if required_margin > available_cash:
            return {"error": f"insufficient cash — need ${required_margin:,.0f}, have ${available_cash:,.0f}"}

    result = {
        "simulated": True,
        "action":    action,
        "volume":    volume,
        "price":     current_price,
        "size_usd":  round(volume * current_price, 2),
        "leverage":  leverage,
    }
    print(f"  [sim]  {action.upper()} {volume} {ticker} @ ~${current_price:.2f}  (paper sim — DB tracked)")
    return result


# ---------------------------------------------------------------------------
# Per-ticker AI pipeline
# ---------------------------------------------------------------------------

async def process_ticker(ticker_row: dict, flags: list[str], all_indicators: dict, timer_driven: bool = False, current_price: float | None = None) -> None:
    ticker      = ticker_row["ticker"]
    asset_class = ticker_row.get("asset_class", "stock")

    trigger_label = ",".join(flags) if flags else ""
    if timer_driven:
        trigger_label = (trigger_label + ",timer") if trigger_label else "timer"

    print(f"\n  [AI ▶] {ticker}  trigger={trigger_label}")
    # Stamp last_ai_run + set cooldown immediately — prevents re-triggering
    # if the next poll cycle starts while this slow LLM pipeline is still running.
    db.stamp_ai_run_started(ticker)
    db.set_cooldown(ticker, COOLDOWN_MIN)
    update_ticker_state(ticker, stage="context_fetch")

    # Fetch non-social context in parallel — X data is fetched by the social agent itself
    # current_price may already be passed in from _process_one_ticker to avoid redundant CLI calls
    loop = asyncio.get_running_loop()
    fetches = {
        "open_position":    loop.run_in_executor(None, lambda: db.get_open_position(ticker)),
        "settings":         loop.run_in_executor(None, db.get_settings),
        "decision_history": loop.run_in_executor(None, lambda: db.get_ticker_decision_history(ticker, limit=5)),
        "all_open":         loop.run_in_executor(None, db.get_all_open_positions),
        "realized_pnl":     loop.run_in_executor(None, db.get_realized_pnl),
    }
    if current_price is None:
        fetches["price"] = loop.run_in_executor(None, lambda: get_current_price(ticker))
    results = await asyncio.gather(*fetches.values())
    fetch_map = dict(zip(fetches.keys(), results))
    if current_price is None:
        current_price = fetch_map["price"]
    open_position      = fetch_map["open_position"]
    settings           = fetch_map["settings"]
    decision_history   = fetch_map["decision_history"]
    all_open_positions = fetch_map["all_open"]
    realized_pnl       = fetch_map["realized_pnl"]

    # Build portfolio summary for decision + risk agents
    paper_capital = settings.get("paper_capital", 1000.0)
    portfolio_summary = db.build_portfolio_summary(
        paper_capital=paper_capital,
        all_open_positions=all_open_positions,
        realized_pnl=realized_pnl,
        current_ticker=ticker,
        current_price=current_price,
    )

    # Enrich open_position with P&L + time-in-trade before passing to orchestrator
    enriched_position = None
    if open_position and current_price:
        enriched_position = db.enrich_position(open_position, current_price)

    # Build unified context — orchestrator dispatches technical_analyst, social_analyst,
    # risk_manager sub-agents internally via the multi-agent model.
    full_context = {
        "ticker":            ticker,
        "current_price":     current_price,
        "indicators":        all_indicators,              # for technical_analyst
        "flags":             flags,                       # for technical_analyst + risk_manager
        "x_search_query":    build_x_query(ticker_row),  # social_analyst will call x_search
        "obv":               {tf: v.get("obv") for tf, v in all_indicators.items()},
        "atr":               {tf: v.get("atr") for tf, v in all_indicators.items()},
        "portfolio_summary": portfolio_summary,
        "open_position":     enriched_position,
        "decision_history":  decision_history,
    }

    print(f"  [AI]  Launching multi-agent analysis (technical / social / risk)...")
    update_ticker_state(ticker, stage="specialists")

    # Progress callback — updates dashboard state as each sub-agent starts/finishes
    _specialist_status = {}  # tracks which specialists are done
    async def _on_progress(stage, data):
        if stage == "specialists_start":
            update_ticker_state(ticker, stage="specialists", sub_agents={})
        elif stage.endswith("_start") and stage != "decision_start":
            name = stage.removesuffix("_start")
            _specialist_status[name] = "running"
            update_ticker_state(ticker, stage="specialists", sub_agents=dict(_specialist_status))
        elif stage.endswith("_done") and stage != "decision_done":
            name = stage.removesuffix("_done")
            _specialist_status[name] = "done"
            update_ticker_state(ticker, stage="specialists", sub_agents=dict(_specialist_status))
        elif stage == "decision_start":
            update_ticker_state(ticker, stage="decision")
        elif stage == "decision_done":
            update_ticker_state(ticker, stage="execution")

    result = await run_orchestrated_decision(full_context, settings, on_progress=_on_progress)

    # Extract specialist outputs + decision fields from unified response
    technical = result.get("technical_analysis", {})
    social    = result.get("social_analysis", {})
    risk      = result.get("risk_analysis", {})
    decision  = result   # action, size_usd, leverage, etc. live at top level

    print(f"  [AI]  Technical : {technical.get('signal','?')} ({technical.get('confidence','?')}%) — {technical.get('pattern','')}")
    print(f"  [AI]  Social    : {social.get('signal','?')} ({social.get('confidence','?')}%) — hype={social.get('hype_vs_real','?')}")
    print(f"  [AI]  Risk      : max ${risk.get('max_position_usd','?')} lev={risk.get('recommended_leverage','?')}x")

    # Guard: abort if orchestrator returned a hard error with no action
    if isinstance(result, dict) and "error" in result and "action" not in result:
        print(f"  [ABORT] {ticker}: orchestrator error — {result['error']}")
        update_ticker_state(ticker, status="error", error=f"orchestrator_failure: {result['error']}")
        db.update_signal_state(ticker, {"action": "hold"}, trigger_label)
        return

    # Store specialist results in state for dashboard
    update_ticker_state(ticker,
        specialist_technical=technical,
        specialist_social=social,
        specialist_risk=risk,
    )

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

    # Guard: short positions require minimum 2x leverage (borrowing is mandatory)
    if action == "short" and (decision.get("leverage") or 1) < 2:
        decision["leverage"] = 2
        print(f"  [GUARD] {ticker}: short requires min 2x leverage — forced to 2x")

    print(f"  [AI ◀] {ticker} → {action.upper()} (confidence={decision.get('confidence','?')}%)")
    print(f"         {decision.get('reasoning','')[:140]}")

    # Log AI decision session to Supabase (no financials — those go to the trades table)
    agent_log_id = db.log_agent_run(
        ticker=ticker,
        action=action,
        technical=technical,
        social=social,
        risk=risk,
        decision_reasoning=decision.get("reasoning", ""),
        decision_json=decision,
        pair=ticker,
        trigger_flags=trigger_label,
        position_side=open_position.get("side") if open_position else "flat",
        indicators_snapshot=all_indicators,
        executed=False,
    )

    # Execute if not hold
    if action == "hold":
        pass
    elif not current_price:
        print(f"  [SKIP] {ticker}: no current price — not executing.")
    else:
        result = await execute_trade(ticker_row, decision, current_price)
        if result.get("error"):
            print(f"  [EXEC ERROR] {ticker}: {result['error']}")
        else:
            exec_volume = result.get("volume", 0)
            _exec_notional = round(exec_volume * current_price, 2)
            _leverage = result.get("leverage") or 1
            if action in ("buy", "short"):
                db.log_transaction(
                    agent_log_id=agent_log_id,
                    ticker=ticker,
                    action=action,
                    current_price=current_price,
                    volume=exec_volume,
                    notional_usd=_exec_notional,
                    leverage=_leverage,
                    fee=db.calc_trade_fee(_exec_notional),
                )
                db.mark_agent_run_executed(agent_log_id)
                db.open_position(
                    ticker=ticker,
                    side="long" if action == "buy" else "short",
                    quantity=exec_volume,
                    entry_price=current_price,
                    stop_loss=decision.get("stop_loss"),
                    leverage=_leverage,
                    agent_log_id=agent_log_id,
                )
            else:
                closed = db.close_position(ticker, current_price, "ai_signal")
                _realized_pnl = closed.get("realized_pnl") if closed else None
                db.log_transaction(
                    agent_log_id=agent_log_id,
                    ticker=ticker,
                    action=action,
                    current_price=current_price,
                    volume=exec_volume,
                    notional_usd=_exec_notional,
                    leverage=_leverage,
                    fee=db.calc_trade_fee(_exec_notional),
                    realized_pnl=_realized_pnl,
                )
                db.mark_agent_run_executed(agent_log_id)

    # Persist signal state + set cooldown
    db.update_signal_state(ticker, decision, trigger_label)
    db.set_cooldown(ticker, COOLDOWN_MIN)
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
    Crypto trades 24/7. Stocks follow NYSE hours Mon-Fri 09:30-16:00 ET."""
    if asset_class != "stock":
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
    ticker      = ticker_row["ticker"]
    asset_class = ticker_row.get("asset_class", "stock")
    loop        = asyncio.get_running_loop()
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
        current_price, open_pos = await asyncio.gather(
            loop.run_in_executor(None, lambda: get_current_price(ticker)),
            loop.run_in_executor(None, lambda: db.get_open_position(ticker)),
        )
        update_ticker_state(ticker, open_position=open_pos)

        # 4. Stop-loss check + trailing stop update (no AI — immediate / mechanical)
        if open_pos and current_price:
            sl   = open_pos.get("stop_loss")
            side = open_pos.get("side")

            # --- Trailing stop: ratchet stop-loss toward price using ATR ---
            # Prefer 4h ATR for stocks (daily instruments; 1h is too noisy).
            # Fall back to 1h ATR for crypto where 1h is the primary timeframe.
            atr_4h = (all_indicators.get("4h") or {}).get("atr")
            atr_1h = (all_indicators.get("1h") or {}).get("atr")
            trail_atr = (atr_4h if asset_class == "stock" else None) or atr_1h
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

        # 5. Market hours gate — skip AI for stocks when exchange is closed
        if not is_market_open(asset_class):
            print(f"  [skip] {ticker}: market closed for {asset_class} — skipping AI pipeline")
            update_ticker_state(ticker, status="skipped", stage="complete", skip_reason="market closed")
            return

        # 6. Collect all threshold flags
        flags = ind.any_flags(all_indicators)
        update_ticker_state(ticker, stage="flag_check", flags=flags, current_price=current_price)

        # 7. Fetch last AI run + cooldown state
        last_ai, on_cooldown = await asyncio.gather(
            loop.run_in_executor(None, lambda: db.get_last_ai_run(ticker)),
            loop.run_in_executor(None, lambda: db.check_cooldown(ticker)),
        )

        timer_expired = (
            last_ai is None
            or (datetime.now(timezone.utc) - last_ai).total_seconds() >= AI_TIMER_MIN * 60
        )
        active_flags = [] if on_cooldown else flags

        # 8. Trigger AI if there are new flags or timer expired
        if active_flags or timer_expired:
            update_ticker_state(ticker, stage="ai_pipeline", active_flags=active_flags, timer_expired=timer_expired)
            await process_ticker(ticker_row, active_flags, all_indicators, timer_driven=timer_expired, current_price=current_price)
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

    mode_label = "paper"
    print(f"[bot] Starting — mode={mode_label}  poll={POLL_INTERVAL_SEC}s  ai_timer={AI_TIMER_MIN}min  cooldown={COOLDOWN_MIN}min")

    while True:
        # Refresh timing settings each cycle so UI changes take effect
        settings = db.get_settings()
        POLL_INTERVAL_SEC = int(settings.get("poll_interval_sec", 300))
        AI_TIMER_MIN      = int(settings.get("ai_timer_min",      60))
        COOLDOWN_MIN      = int(settings.get("cooldown_min",      30))

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
