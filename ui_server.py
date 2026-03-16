"""
ui_server.py — FastAPI dashboard for the xStocks trading bot.

Run with:
    uvicorn ui_server:app --reload --port 8000
    then open http://localhost:8000
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

import db
import fetch as fetcher
import indicators as ind
import bot
from core import run_kraken, search_x, search_x_stream, Mode, MODE
from agents.technical import analyze as technical_analyze
from agents.social    import analyze as social_analyze
from agents.risk      import analyze as risk_analyze
from agents.decision  import analyze as decision_analyze


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the bot loop as a background task inside uvicorn's event loop."""
    task = asyncio.create_task(bot.fast_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="xStocks Bot Dashboard", lifespan=lifespan)

# Jinja2 template rendering
from jinja2 import Environment, FileSystemLoader
_TEMPLATE_DIR = Path(__file__).parent / "ui" / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))

# Serve static files (CSS, JS)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "ui" / "static")), name="static")

# ---------------------------------------------------------------------------
# Live bot-run state — written by bot.py hooks via shared module state
# Each ticker gets a dict:
#   { ticker, status, stage, flags, started_at, updated_at, last_result }
# status: idle | running | done | error
# stage: candles | indicators | price_check | stop_loss | ai_pipeline |
#        specialists | decision | execution | complete | skipped
# ---------------------------------------------------------------------------
from bot_state import (
    update_ticker_state, get_ticker_states,
    update_cycle_state, get_cycle_state, _cycle_state,
)


# ---------------------------------------------------------------------------
# JSON serialization helper (handles numpy/datetime types from indicators)
# ---------------------------------------------------------------------------

def _safe(obj):
    if isinstance(obj, dict):
        return {k: _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    try:
        import numpy as np
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    except ImportError:
        pass
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def sse(data: dict) -> str:
    return f"data: {json.dumps(_safe(data))}\n\n"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self' data: https://cdn.jsdelivr.net;"
)

@app.get("/", response_class=HTMLResponse)
async def index():
    html = _jinja_env.get_template("base.html").render()
    return HTMLResponse(content=html, headers={"Content-Security-Policy": _CSP})


@app.get("/api/watchlist")
async def api_watchlist():
    return db.get_all_watchlist_tickers()


@app.post("/api/watchlist")
async def api_add_watchlist(request: Request):
    body = await request.json()
    ticker = (body.get("ticker") or "").strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    row = {
        "ticker":      ticker,
        "source":      body.get("source", "kraken_crypto"),
        "pair":        body.get("pair") or ticker,
        "asset_class": body.get("asset_class", "spot"),
        "search_name": body.get("search_name") or None,
        "active":      True,
    }
    try:
        r = db.get_client().table("watchlist").insert(row).execute()
        return r.data[0] if r.data else row
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail=f"Ticker '{ticker}' already exists")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/watchlist/{ticker}")
async def api_patch_watchlist(ticker: str, request: Request):
    body = await request.json()
    allowed = {"active", "search_name", "pair", "asset_class", "source"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    try:
        r = db.get_client().table("watchlist").update(updates).eq("ticker", ticker).execute()
        if not r.data:
            raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found")
        return r.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/watchlist/{ticker}")
async def api_delete_watchlist(ticker: str):
    try:
        r = db.get_client().table("watchlist").delete().eq("ticker", ticker).execute()
        if not r.data:
            raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found")
        return {"deleted": ticker}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/kraken-pairs")
async def api_kraken_pairs(q: str = ""):
    """Search Kraken tradable pairs. Queries both spot and tokenized_asset classes."""
    if len(q) < 1:
        raise HTTPException(status_code=400, detail="query 'q' required (min 1 char)")
    loop = asyncio.get_running_loop()
    query_upper = q.upper()

    async def fetch_pairs(aclass: str | None):
        args = ["pairs"]
        if aclass:
            args += ["--aclass", aclass]
        return await loop.run_in_executor(None, lambda: run_kraken(args))

    spot_data, xstock_data = await asyncio.gather(
        fetch_pairs(None),
        fetch_pairs("tokenized_asset"),
    )

    results = []
    seen = set()
    for data, source, ac in [
        (xstock_data, "kraken_xstock", "tokenized_asset"),
        (spot_data, "kraken_crypto", "spot"),
    ]:
        if not isinstance(data, dict) or "error" in data:
            continue
        for pair_key, info in data.items():
            if not isinstance(info, dict):
                continue
            altname = info.get("altname", "")
            base = info.get("base", "")
            wsname = info.get("wsname", "")
            # Match against query
            if not (query_upper in pair_key.upper() or
                    query_upper in altname.upper() or
                    query_upper in base.upper() or
                    query_upper in wsname.upper()):
                continue
            # Only USD-quoted pairs, skip duplicates
            quote = info.get("quote", "")
            if quote not in ("ZUSD", "USD"):
                continue
            if pair_key in seen:
                continue
            seen.add(pair_key)
            # Derive a search_name
            if ac == "tokenized_asset":
                sname = base.removesuffix("x") if base.endswith("x") else base
            else:
                sname = altname.replace("USD", "").replace("XBT", "Bitcoin BTC").replace("ETH", "Ethereum ETH") or base
            results.append({
                "pair":         pair_key,
                "altname":      altname,
                "base":         base,
                "wsname":       wsname,
                "source":       source,
                "asset_class":  ac,
                "search_name":  sname,
            })
    # Sort: xStocks first, then by altname
    results.sort(key=lambda r: (0 if r["source"] == "kraken_xstock" else 1, r["altname"]))
    return results[:50]


@app.get("/api/positions")
async def api_positions():
    loop = asyncio.get_running_loop()

    # Kraken cash balance
    balance_cmd = ["paper", "balance"] if MODE == Mode.PAPER else ["balance"]
    balance = await loop.run_in_executor(None, lambda: run_kraken(balance_cmd))

    # Get current prices — union of active watchlist + any ticker with an open position
    watchlist_all    = db.get_all_watchlist_tickers()
    open_tickers     = {p["ticker"] for p in db.get_all_open_positions()}
    watchlist_tickers = {r["ticker"] for r in watchlist_all}
    price_rows = [r for r in watchlist_all
                  if r.get("active") or r["ticker"] in open_tickers]
    # For open positions whose ticker isn't in the watchlist at all, fall back to
    # using the ticker itself as the Kraken pair name (e.g. "SOLUSD").
    for t in open_tickers - watchlist_tickers:
        price_rows.append({"ticker": t, "pair": t, "asset_class": "spot"})
    async def fetch_price(row):
        try:
            p = await loop.run_in_executor(
                None,
                lambda r=row: bot.get_current_price(
                    r.get("pair", r["ticker"]),
                    r.get("asset_class", "spot"),
                ),
            )
            return row["ticker"], p
        except Exception:
            return row["ticker"], None

    results = await asyncio.gather(*[fetch_price(row) for row in price_rows])
    prices = {ticker: p for ticker, p in results if p is not None}

    # Open positions from positions table (source of truth)
    raw_positions = db.get_all_open_positions()
    open_positions = []
    for pos in raw_positions:
        ticker   = pos["ticker"]
        entry    = pos.get("entry_price") or 0
        current  = prices.get(ticker)
        volume   = pos.get("quantity") or 0
        lev      = pos.get("leverage", 1)
        side     = pos.get("side", "long")
        pnl = None
        if current and entry and volume:
            pnl = round(
                (current - entry) * volume * lev if side == "long"
                else (entry - current) * volume * lev,
                2,
            )
        notional    = entry * volume
        margin_cost = round(db.calc_margin_cost(notional, lev, pos.get("opened_at")), 2)
        open_positions.append({
            "ticker":        ticker,
            "action":        side,
            "entry_price":   entry,
            "current_price": current,
            "size_usd":      round(entry * volume, 2),
            "leverage":      lev,
            "volume":        volume,
            "stop_loss":     pos.get("stop_loss"),
            "pnl":           pnl,
            "reasoning":     "",    # fetched separately below
            "opened_at":     pos.get("opened_at"),
            "trade_id":      pos.get("agent_log_id"),
            "margin_cost":   margin_cost,
        })

    # Enrich reasoning from agent_log
    if open_positions:
        trade_ids = [p["trade_id"] for p in open_positions if p["trade_id"]]
        if trade_ids:
            reasons = db.get_recent_agent_runs(limit=200)["data"]
            reasons_map = {t["id"]: t.get("decision_reasoning", "") for t in reasons}
            for p in open_positions:
                p["reasoning"] = reasons_map.get(p["trade_id"], "")

    # Recent trades from transaction_ledger
    trades = await loop.run_in_executor(None, lambda: db.get_recent_transactions(30))

    # Computed portfolio summary
    total_size   = sum((p["entry_price"] or 0) * (p["volume"] or 0) for p in open_positions)
    total_cost   = sum(
        ((p["entry_price"] or 0) * (p["volume"] or 0)) / max(p["leverage"] or 1, 1)
        for p in open_positions
    )
    total_pnl    = sum(p["pnl"] for p in open_positions if p["pnl"] is not None)
    pnl_pct      = round((total_pnl / total_cost) * 100, 2) if total_cost else None

    # Available cash derived from DB settings (paper_capital) + realized P&L.
    settings      = await loop.run_in_executor(None, db.get_settings)
    paper_capital = settings["paper_capital"]
    realized_pnl  = await loop.run_in_executor(None, db.get_realized_pnl)
    available_cash = round(paper_capital - total_cost + realized_pnl, 2)

    closed_positions = await loop.run_in_executor(None, lambda: db.get_closed_positions_full(50))

    # Total fees = entry fee on open positions + both sides on closed positions
    open_entry_fees = sum(
        db.calc_trade_fee((p.get("entry_price") or 0) * (p.get("quantity") or 0))
        for p in open_positions
    )
    closed_fees = sum(
        db.calc_trade_fee((p.get("entry_price") or 0) * (p.get("quantity") or 0))
        + db.calc_trade_fee((p.get("close_price") or 0) * (p.get("quantity") or 0))
        for p in closed_positions
    )
    total_fees = open_entry_fees + closed_fees
    # Margin cost = accrued interest on leveraged closed positions only
    total_margin_cost = sum((p.get("margin_cost") or 0) for p in closed_positions)

    summary = {
        "cash":              available_cash,
        "paper_capital":     paper_capital,
        "realized_pnl":      round(realized_pnl, 2),
        "total_size":        round(total_size, 2),
        "total_cost":        round(total_cost, 2),
        "total_pnl":         round(total_pnl, 2),
        "pnl_pct":           pnl_pct,
        "pos_count":         len(open_positions),
        "total_fees":        round(total_fees, 2),
        "total_margin_cost": round(total_margin_cost, 2),
    }

    return {
        "mode":             MODE.value,
        "balance":          balance,
        "summary":          summary,
        "settings":         settings,
        "open_positions":   open_positions,
        "closed_positions": closed_positions,
        "recent_trades":    trades,
        "prices":           prices,
    }


@app.get("/api/settings")
async def api_get_settings():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, db.get_settings)


@app.put("/api/settings")
async def api_put_settings(request: Request):
    body = await request.json()
    # Validate: only known keys, basic type checks
    allowed_keys = set(db._DEFAULT_SETTINGS.keys())
    unknown = set(body.keys()) - allowed_keys
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown settings keys: {unknown}")
    loop = asyncio.get_running_loop()
    updated = await loop.run_in_executor(None, lambda: db.update_settings(body))
    return updated


# ---------------------------------------------------------------------------
# SSE agent stream
# ---------------------------------------------------------------------------

async def _agent_stream(ticker: str) -> AsyncGenerator[str, None]:
    try:
        # Resolve ticker row (allow inactive tickers for manual UI runs)
        all_rows = db.get_all_watchlist_tickers()
        ticker_row = next((r for r in all_rows if r["ticker"] == ticker), None)
        if not ticker_row:
            yield sse({"step": "error", "msg": f"Ticker '{ticker}' not found in watchlist table."})
            return

        loop = asyncio.get_running_loop()

        # ── 1. Candles ───────────────────────────────────────────────────────
        yield sse({"step": "candles_start", "ticker": ticker})
        await loop.run_in_executor(None, lambda: fetcher.update_candles(ticker_row))
        yield sse({"step": "candles_done", "ticker": ticker})

        # ── 2. Indicators ────────────────────────────────────────────────────
        yield sse({"step": "indicators_start", "ticker": ticker})
        all_indicators = await loop.run_in_executor(
            None, lambda: ind.compute_all_timeframes(ticker_row)
        )
        flags = ind.any_flags(all_indicators)
        slim_ind = {
            tf: {
                k: v for k, v in d.items()
                if k in ("rsi", "macd", "ema_20", "ema_50", "atr", "latest_close", "threshold_flags")
            }
            for tf, d in all_indicators.items()
        }
        yield sse({"step": "indicators_done", "ticker": ticker,
                   "flags": flags, "indicators": slim_ind})

        # ── 3. Current price ─────────────────────────────────────────────────
        pair        = ticker_row.get("pair", ticker)
        asset_class = ticker_row.get("asset_class", "spot")
        current_price = await loop.run_in_executor(
            None, lambda: bot.get_current_price(pair, asset_class)
        )
        yield sse({"step": "price", "ticker": ticker, "price": current_price})

        # ── 4. X social search (streamed) ──────────────────────────────────────
        from core import get_search_name, build_x_query
        search_name = get_search_name(ticker_row)
        x_query = build_x_query(ticker_row)
        yield sse({"step": "social_start", "ticker": ticker,
                   "query": search_name})

        # Stream chunks from xAI Responses API in a thread so we don't block asyncio
        x_chunks: list[str] = []

        def _stream_x():
            for chunk in search_x_stream(x_query):
                x_chunks.append(chunk)

        # Kick off the blocking generator in an executor,
        # but drain chunks to the SSE client as they arrive.
        stream_task = loop.run_in_executor(None, _stream_x)

        # Poll for new chunks while the background thread is running
        sent = 0
        while not stream_task.done():
            await asyncio.sleep(0.15)
            while sent < len(x_chunks):
                yield sse({"step": "social_chunk", "ticker": ticker,
                           "chunk": x_chunks[sent]})
                sent += 1
        await stream_task  # propagate any exception

        # Flush any remaining chunks
        while sent < len(x_chunks):
            yield sse({"step": "social_chunk", "ticker": ticker,
                       "chunk": x_chunks[sent]})
            sent += 1

        x_data = "".join(x_chunks)
        yield sse({"step": "social_data", "ticker": ticker,
                   "snippet": x_data[:600]})

        # ── 5. Holdings ──────────────────────────────────────────────────────
        balance_cmd = ["paper", "balance"] if MODE == Mode.PAPER else ["balance"]
        holdings = await loop.run_in_executor(None, lambda: run_kraken(balance_cmd))

        # ── 6. Build specialist inputs ───────────────────────────────────────
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
        settings = await loop.run_in_executor(None, db.get_settings)
        risk_context = {
            "ticker":        ticker,
            "current_price": current_price,
            "atr":           {tf: v.get("atr") for tf, v in all_indicators.items()},
            "holdings":      holdings,
            "flags":         flags,
            "settings":      settings,
        }

        # ── 7. Specialists (parallel) ────────────────────────────────────────
        yield sse({"step": "specialists_start", "ticker": ticker})
        technical, social_result, risk = await asyncio.gather(
            technical_analyze(technical_context),
            social_analyze(social_context),
            risk_analyze(risk_context),
        )
        yield sse({"step": "technical_done", "ticker": ticker, "result": technical})
        yield sse({"step": "social_agent_done", "ticker": ticker, "result": social_result})
        yield sse({"step": "risk_done", "ticker": ticker, "result": risk})

        # ── 8. Decision ──────────────────────────────────────────────────────
        yield sse({"step": "decision_start", "ticker": ticker})
        open_position = await loop.run_in_executor(None, lambda: db.get_open_position(ticker))
        decision_context = {
            "ticker":             ticker,
            "current_price":      current_price,
            "open_position":      open_position,
            "current_holdings":   holdings,
            "technical_analysis": technical,
            "social_analysis":    social_result,
            "risk_analysis":      risk,
        }
        decision = await decision_analyze(decision_context)
        yield sse({"step": "decision_done", "ticker": ticker, "result": decision})

        # ── 9. Log + Execute ─────────────────────────────────────────────────
        action = decision.get("action", "hold")

        # Position guard — same rules as bot.py
        if open_position:
            side = open_position.get("side")
            if side == "short" and action not in ("hold", "cover"):
                action = "hold"
                yield sse({"step": "guard", "ticker": ticker, "msg": "Already short — overriding to hold"})
            elif side == "long" and action not in ("hold", "sell"):
                action = "hold"
                yield sse({"step": "guard", "ticker": ticker, "msg": "Already long — overriding to hold"})
        else:
            if action in ("sell", "cover"):
                action = "hold"
                yield sse({"step": "guard", "ticker": ticker, "msg": "No open position — overriding to hold"})

        size_usd_ui = decision.get("size_usd") or 0

        agent_log_id = db.log_agent_run(
            ticker=ticker,
            action=action,
            technical=technical,
            social=social_result,
            risk=risk,
            decision_reasoning=decision.get("reasoning", ""),
            decision_json=decision,
            pair=ticker_row.get("pair"),
            trigger_flags="ui_trigger",
            executed=False,
        )
        trade_id = agent_log_id  # used below for position linking
        db.update_signal_state(ticker, decision, "ui_trigger")

        if action != "hold" and current_price and decision.get("size_usd"):
            # Enforce the same hard limits as bot.py
            skip_reason = None
            if action in ("buy", "short"):
                all_open = await loop.run_in_executor(None, db.get_all_open_positions)
                _settings = await loop.run_in_executor(None, db.get_settings)
                if len(all_open) >= _settings.get("max_open_positions", 10):
                    skip_reason = f"max_open_positions ({_settings['max_open_positions']}) reached"
                else:
                    paper_capital = _settings.get("paper_capital", 1000.0)
                    max_pct_usd   = paper_capital * _settings.get("max_position_pct", 20) / 100
                    max_hard_usd  = _settings.get("max_position_usd") or max_pct_usd
                    size_cap      = min(max_pct_usd, max_hard_usd)
                    if (decision.get("size_usd") or 0) > size_cap:
                        decision["size_usd"] = round(size_cap, 2)
                    max_lev = _settings.get("max_leverage", 3)
                    if (decision.get("leverage") or 1) > max_lev:
                        decision["leverage"] = max_lev
                    used_margin    = sum(((p["entry_price"] or 0) * (p["quantity"] or 0)) / max(p["leverage"] or 1, 1) for p in all_open)
                    realized       = await loop.run_in_executor(None, db.get_realized_pnl)
                    available_cash = paper_capital - used_margin + realized
                    required_margin = (decision.get("size_usd") or 0) / max(decision.get("leverage", 1), 1)
                    if required_margin > available_cash:
                        skip_reason = f"insufficient cash — need ${required_margin:,.0f}, have ${available_cash:,.0f}"

            if skip_reason:
                yield sse({"step": "trade_skipped", "ticker": ticker, "reason": skip_reason})
            else:
                yield sse({"step": "trade_start", "ticker": ticker, "action": action,
                           "size_usd": decision.get("size_usd"), "price": current_price})
                exec_result = await bot.execute_trade(ticker_row, decision, current_price)
                exec_volume = exec_result.get("volume") or round(
                    (decision.get("size_usd") or 0) / (current_price or 1), 4
                )
                db.log_transaction(
                    agent_log_id=agent_log_id,
                    ticker=ticker,
                    action=action,
                    current_price=current_price or 0,
                    volume=exec_volume,
                    notional_usd=size_usd_ui,
                    leverage=decision.get("leverage", 1),
                    stop_loss=decision.get("stop_loss"),
                    fee=0.0,
                    pair=ticker_row.get("pair"),
                    order_type="market",
                    source_type="paper",
                    is_simulated=exec_result.get("simulated", False),
                    execution_result=exec_result,
                )
                db.mark_agent_run_executed(agent_log_id)

                # Record position change (mirrors bot.py logic)
                if action in ("buy", "short") and not exec_result.get("error"):
                    volume = exec_volume
                    await loop.run_in_executor(None, lambda: db.open_position(
                        ticker=ticker,
                        side="long" if action == "buy" else "short",
                        quantity=volume,
                        entry_price=current_price,
                        stop_loss=decision.get("stop_loss"),
                        leverage=decision.get("leverage", 1),
                        agent_log_id=agent_log_id,
                    ))
                elif action in ("sell", "cover") and not exec_result.get("error"):
                    await loop.run_in_executor(None, lambda: db.close_position(ticker, current_price, "ai_signal"))

                yield sse({"step": "trade_done", "ticker": ticker, "result": exec_result})
        else:
            yield sse({"step": "trade_skipped", "ticker": ticker,
                       "reason": "hold" if action == "hold" else "missing size or price"})

        yield sse({"step": "complete", "ticker": ticker})

    except Exception as e:
        import traceback
        yield sse({"step": "error", "msg": str(e),
                   "trace": traceback.format_exc()[-800:]})


@app.get("/stream/agent/{ticker}")
async def stream_agent(ticker: str):
    return StreamingResponse(
        _agent_stream(ticker),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Bot live status — current state of each ticker being processed by bot.py
# ---------------------------------------------------------------------------

@app.get("/api/bot-status")
async def api_bot_status():
    """Return live pipeline state for all tickers in the current bot cycle."""
    import time
    states = get_ticker_states()
    watchlist = db.get_all_watchlist_tickers()
    # Build full list: active tickers + any that have state
    tickers_with_state = set(states.keys())
    active_tickers = {r["ticker"] for r in watchlist if r.get("active")}
    all_tickers = active_tickers | tickers_with_state

    now = time.time()
    result = []
    for ticker in sorted(all_tickers):
        state = states.get(ticker)
        if state:
            # Stale if last update > 10 minutes ago and not idle
            age = now - state.get("updated_at", now)
            if age > 600 and state.get("status") not in ("idle", "done", "error", "skipped"):
                state = dict(state, status="idle", stage=None)
            result.append(state)
        else:
            result.append({"ticker": ticker, "status": "idle", "stage": None,
                           "flags": [], "started_at": None, "updated_at": None})
    return {"tickers": result, "cycle": _cycle_state}


@app.get("/api/agent-history")
async def api_agent_history(page: int = 1):
    """Return paginated agent log entries (summary columns only — no heavy JSON blobs)."""
    per_page = 40
    if page < 1:
        page = 1
    offset = (page - 1) * per_page
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: db.get_recent_agent_runs(limit=per_page, offset=offset))
    total = result["total"]
    total_pages = max(1, -(-total // per_page))  # ceil division
    return {"data": result["data"], "page": page, "per_page": per_page, "total": total, "total_pages": total_pages}


@app.get("/api/agent-log/{agent_log_id}")
async def api_agent_detail(agent_log_id: int):
    """Return full detail for a single agent_log row plus linked transaction_ledger records."""
    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(None, lambda: db.get_agent_run_by_id(agent_log_id))
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Agent run not found")
    txns = await loop.run_in_executor(None, lambda: db.get_transactions_for_agent(agent_log_id))
    row["transactions"] = txns
    return row
