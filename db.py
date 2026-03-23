"""
db.py — Supabase client and all database operations.
Requires SUPABASE_URL and SUPABASE_KEY in .env
"""

import os
from datetime import datetime, timezone
from typing import Optional

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
        _client = create_client(url, key)
    return _client


def reset_client() -> None:
    """Drop the cached client so next call to get_client() creates a fresh one."""
    global _client
    _client = None


def _is_conn_err(e: Exception) -> bool:
    s = f"{type(e)} {e}"
    return any(x in s for x in (
        "RemoteProtocolError", "ReadError", "ConnectionTerminated",
        "ConnectError", "Errno 35", "ConnectTimeout",
    ))


def _exec(fn):
    """Run a Supabase query lambda, resetting the client and retrying once on HTTP/2 drops."""
    try:
        return fn()
    except Exception as e:
        if _is_conn_err(e):
            reset_client()
            return fn()
        raise


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def get_watchlist() -> list[dict]:
    r = _exec(lambda: get_client().table("watchlist").select("*").eq("active", True).execute())
    return r.data


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------

def upsert_candles(rows: list[dict]) -> None:
    if rows:
        _exec(lambda: get_client().table("candles").upsert(rows, on_conflict="ticker,timeframe,ts").execute())


def get_candle_window(ticker: str, timeframe: str, limit: int = 100) -> list[dict]:
    """Return the most recent N candles in chronological order."""
    r = _exec(lambda: (
        get_client().table("candles")
        .select("*")
        .eq("ticker", ticker)
        .eq("timeframe", timeframe)
        .order("ts", desc=True)
        .limit(limit)
        .execute()
    ))
    return list(reversed(r.data))


def get_latest_candle_ts(ticker: str, timeframe: str) -> Optional[str]:
    r = _exec(lambda: (
        get_client().table("candles")
        .select("ts")
        .eq("ticker", ticker)
        .eq("timeframe", timeframe)
        .order("ts", desc=True)
        .limit(1)
        .execute()
    ))
    return r.data[0]["ts"] if r.data else None


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

_EPHEMERAL_INDICATOR_KEYS = {"latest_close", "latest_open", "latest_high", "latest_low", "latest_volume"}

def upsert_indicators(row: dict) -> None:
    db_row = {k: v for k, v in row.items() if k not in _EPHEMERAL_INDICATOR_KEYS}
    _exec(lambda: get_client().table("indicators").upsert(db_row, on_conflict="ticker,timeframe,ts").execute())


# ---------------------------------------------------------------------------
# Signal state (last AI output + 60-min timer)
# ---------------------------------------------------------------------------

def get_signal_state(ticker: str) -> Optional[dict]:
    r = get_client().table("signal_state").select("*").eq("ticker", ticker).execute()
    return r.data[0] if r.data else None


def stamp_ai_run_started(ticker: str) -> None:
    """Write last_ai_run NOW so a crash mid-pipeline won't re-trigger on restart."""
    get_client().table("signal_state").upsert(
        {
            "ticker": ticker,
            "last_ai_run": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="ticker",
    ).execute()


def update_signal_state(ticker: str, signal: dict, event_type: str) -> None:
    get_client().table("signal_state").upsert(
        {
            "ticker": ticker,
            "last_signal": signal,
            "last_event_type": event_type,
            "last_ai_run": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="ticker",
    ).execute()


def get_last_ai_run(ticker: str) -> Optional[datetime]:
    state = get_signal_state(ticker)
    if state and state.get("last_ai_run"):
        ts = state["last_ai_run"]
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None


# ---------------------------------------------------------------------------
# Cooldown (stored in signal_state.cooldown_until)
# ---------------------------------------------------------------------------

def check_cooldown(ticker: str) -> bool:
    """Return True if ticker is within its cooldown window (should skip)."""
    state = get_signal_state(ticker)
    if not state or not state.get("cooldown_until"):
        return False
    cu = datetime.fromisoformat(state["cooldown_until"].replace("Z", "+00:00"))
    if cu.tzinfo is None:
        cu = cu.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < cu


def set_cooldown(ticker: str, window_minutes: int = 30) -> None:
    """Set cooldown_until on signal_state for this ticker."""
    from datetime import timedelta
    until = (datetime.now(timezone.utc) + timedelta(minutes=window_minutes)).isoformat()
    get_client().table("signal_state").upsert(
        {
            "ticker": ticker,
            "cooldown_until": until,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="ticker",
    ).execute()


# ---------------------------------------------------------------------------
# Agent log
# ---------------------------------------------------------------------------

def log_agent_run(
    ticker: str,
    action: str,
    technical: dict,
    social: dict,
    risk: dict,
    decision_reasoning: str,
    trigger_flags: Optional[str] = None,
    position_side: str = "flat",
    indicators_snapshot: Optional[dict] = None,
    executed: bool = False,
    decision_json: Optional[dict] = None,
    pair: Optional[str] = None,  # deprecated — ignored, kept for call-site compat
) -> int:
    payload = {
        "ticker": ticker,
        "action": action,
        "trigger_flags": trigger_flags,
        "technical_analysis": technical,
        "social_analysis": social,
        "risk_analysis": risk,
        "decision_reasoning": decision_reasoning,
        "decision_json": decision_json,
        "position_side": position_side,
        "indicators_snapshot": indicators_snapshot,
        "executed": executed,
    }
    try:
        r = get_client().table("agent_log").insert(payload).execute()
    except Exception:
        # Graceful fallback: column may not exist in DB yet — retry without it
        payload.pop("indicators_snapshot", None)
        payload.pop("decision_json", None)
        r = get_client().table("agent_log").insert(payload).execute()
    return r.data[0]["id"]


def mark_agent_run_executed(agent_log_id: int) -> None:
    get_client().table("agent_log").update(
        {"executed": True}
    ).eq("id", agent_log_id).execute()


def get_recent_agent_runs(limit: int = 50, offset: int = 0) -> dict:
    """Return paginated agent log entries and total count."""
    client = get_client()
    r = (
        client.table("agent_log")
        .select("id,ticker,action,trigger_flags,decision_reasoning,position_side,executed,ts", count="exact")
        .order("ts", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return {"data": r.data or [], "total": r.count or 0}


def _slim_indicators(snap: dict | None) -> dict | None:
    """Extract key indicator values per timeframe from a full indicators snapshot.
    Keeps the payload small enough for LLM context while preserving trend memory."""
    if not snap or not isinstance(snap, dict):
        return None
    slim = {}
    for tf, v in snap.items():
        if not isinstance(v, dict):
            continue
        slim[tf] = {
            "close":       v.get("latest_close") or v.get("close"),
            "rsi":         v.get("rsi"),
            "macd_hist":   v.get("macd_hist"),
            "ema_20":      v.get("ema_20"),
            "ema_50":      v.get("ema_50"),
            "bb_upper":    v.get("bb_upper"),
            "bb_lower":    v.get("bb_lower"),
            "obv":         v.get("obv"),
            "atr":         v.get("atr"),
        }
    return slim


def get_ticker_decision_history(ticker: str, limit: int = 5) -> list[dict]:
    """Return the last N decision-agent runs for a ticker as compact history entries.
    Used to give the decision agent memory of its own recent decisions."""
    r = _exec(lambda: (
        get_client().table("agent_log")
        .select("ts,action,trigger_flags,decision_reasoning,position_side,executed,indicators_snapshot")
        .eq("ticker", ticker)
        .order("ts", desc=True)
        .limit(limit)
        .execute()
    ))
    rows = r.data or []
    # Slim down indicator snapshots to key values per timeframe
    for row in rows:
        row["indicators"] = _slim_indicators(row.pop("indicators_snapshot", None))
    # Return in chronological order (oldest first) so the LLM reads them as a timeline
    return list(reversed(rows))


def get_agent_run_by_id(agent_log_id: int) -> dict | None:
    """Return a single agent_log row with all columns including specialist analysis blobs."""
    r = (
        get_client().table("agent_log")
        .select("*")
        .eq("id", agent_log_id)
        .limit(1)
        .execute()
    )
    return r.data[0] if r.data else None


def get_transactions_for_agent(agent_log_id: int) -> list:
    """Return all trades rows linked to a given agent_log session."""
    r = (
        get_client().table("trades")
        .select("*")
        .eq("agent_log_id", agent_log_id)
        .order("event_time", desc=False)
        .execute()
    )
    return r.data or []


def log_transaction(
    agent_log_id: int,
    ticker: str,
    action: str,
    current_price: float,
    volume: float,
    notional_usd: float,
    leverage: int,
    fee: float = 0.0,
    realized_pnl: Optional[float] = None,
) -> None:
    """Record a paper trade linked to an agent_log session."""
    side = "buy" if action in ("buy", "cover") else "sell"
    leverage = leverage or 1
    row: dict = {
        "agent_log_id": agent_log_id,
        "ticker":       ticker,
        "side":         side,
        "action":       action,
        "quantity":     volume,
        "price":        current_price,
        "cost":         notional_usd,
        "fee_amount":   fee,
        "leverage":     leverage,
        "status":       "completed",
        "event_time":   datetime.now(timezone.utc).isoformat(),
    }
    if realized_pnl is not None:
        row["realized_pnl"] = realized_pnl
    get_client().table("trades").insert(row).execute()


def get_recent_transactions(limit: int = 30) -> list[dict]:
    """Return recent trades for the Recent Trades UI."""
    r = (
        get_client().table("trades")
        .select("*")
        .order("event_time", desc=True)
        .limit(limit)
        .execute()
    )
    return r.data or []


def get_all_watchlist_tickers() -> list[dict]:
    """Return all watchlist rows including inactive ones."""
    r = _exec(lambda: get_client().table("watchlist").select("*").execute())
    return r.data or []


# ---------------------------------------------------------------------------
# Fee & margin cost helpers
# ---------------------------------------------------------------------------

TRADE_FEE_RATE = 0.0005   # 0.05% per side (taker fee)
MARGIN_APR     = 0.12     # 12% annual interest on borrowed capital


def calc_trade_fee(notional_usd: float) -> float:
    """0.05% taker fee on the full notional value of a trade."""
    return round(notional_usd * TRADE_FEE_RATE, 6)


def calc_margin_cost(notional_usd: float, leverage: int, opened_at: str, closed_at: str = None) -> float:
    """
    12% APR interest on the *borrowed* portion of the position,
    prorated to the time the position was open.
    borrowed = notional * (1 - 1/leverage)
    """
    if leverage <= 1 or not opened_at:
        return 0.0
    try:
        t0 = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        t1 = (
            datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
            if closed_at else datetime.now(timezone.utc)
        )
        days = (t1 - t0).total_seconds() / 86400
    except Exception:
        days = 0.0
    borrowed = notional_usd * (1.0 - 1.0 / leverage)
    return round(borrowed * MARGIN_APR / 365 * days, 6)


# ---------------------------------------------------------------------------
# Positions (open position tracking — one row per ticker, upserted on open)
# ---------------------------------------------------------------------------

def open_position(
    ticker: str,
    side: str,
    quantity: float,
    entry_price: float,
    stop_loss: Optional[float],
    leverage: int,
    agent_log_id: int,
) -> None:
    """Insert a new open position."""
    get_client().table("positions").insert(
        {
            "ticker":        ticker,
            "side":          side,
            "quantity":      quantity,
            "entry_price":   entry_price,
            "stop_loss":     stop_loss,
            "high_water_price": entry_price,
            "leverage":      leverage,
            "agent_log_id":  agent_log_id,
            "opened_at":     datetime.now(timezone.utc).isoformat(),
            "closed_at":     None,
            "close_price":   None,
            "realized_pnl":  None,
            "close_reason":  None,
        },
    ).execute()


def update_trailing_stop(ticker: str, current_price: float, new_stop: float) -> None:
    """Update the high-water mark and trailing stop for an open position."""
    get_client().table("positions").update(
        {"high_water_price": current_price, "stop_loss": round(new_stop, 4)}
    ).eq("ticker", ticker).is_("closed_at", "null").execute()


def close_position(ticker: str, close_price: float, close_reason: str) -> Optional[dict]:
    """Mark the open position for a ticker as closed. Returns the closed row."""
    pos = get_open_position(ticker)
    if not pos:
        return None
    quantity = pos["quantity"]
    entry    = pos["entry_price"]
    lev      = pos.get("leverage") or 1
    raw_pnl = (
        (close_price - entry) * quantity * lev if pos["side"] == "long"
        else (entry - close_price) * quantity * lev
    )
    entry_notional = entry * quantity
    close_notional = close_price * quantity
    fee_in   = calc_trade_fee(entry_notional)
    fee_out  = calc_trade_fee(close_notional)
    margin_c = calc_margin_cost(entry_notional, lev, pos.get("opened_at"))
    pnl = raw_pnl - fee_in - fee_out - margin_c
    now = datetime.now(timezone.utc).isoformat()
    total_fees = round(fee_in + fee_out, 6)
    margin_cost = round(margin_c, 6)
    get_client().table("positions").update({
        "closed_at":     now,
        "close_price":   close_price,
        "realized_pnl":  round(pnl, 2),
        "close_reason":  close_reason,
        "total_fees":    total_fees,
        "margin_cost":   margin_cost,
    }).eq("ticker", ticker).is_("closed_at", "null").execute()
    return {
        **pos,
        "closed_at":    now,
        "close_price":  close_price,
        "realized_pnl": round(pnl, 2),
        "total_fees":   total_fees,
        "margin_cost":  margin_cost,
    }


def get_open_position(ticker: str) -> Optional[dict]:
    r = _exec(lambda: (
        get_client().table("positions")
        .select("*")
        .eq("ticker", ticker)
        .is_("closed_at", "null")
        .order("opened_at", desc=False)
        .limit(1)
        .execute()
    ))
    return r.data[0] if r.data else None


def get_all_open_positions() -> list[dict]:
    r = _exec(lambda: (
        get_client().table("positions")
        .select("*")
        .is_("closed_at", "null")
        .order("opened_at", desc=True)
        .execute()
    ))
    return r.data or []


def get_closed_positions(limit: int = 30) -> list[dict]:
    """Return the most recent closed positions."""
    r = (
        get_client().table("positions")
        .select("*")
        .not_.is_("closed_at", "null")
        .order("closed_at", desc=True)
        .limit(limit)
        .execute()
    )
    return r.data or []


def get_closed_positions_full(limit: int = 50) -> list[dict]:
    """
    Closed positions from two sources merged:
      1. Primary:  positions table rows where closed_at IS NOT NULL.
      2. Fallback: agent_log sell/cover (executed=True) rows that have no matching
                   positions row — reconstructed using trades table prices.
    This ensures history is visible even when a positions row was lost.
    Returned list is sorted by closed_at desc, capped at `limit`.
    """
    real = get_closed_positions(limit)

    # Back-fill total_fees / margin_cost for rows closed before those columns existed
    for p in real:
        if p.get("total_fees") is None:
            entry_notional = (p.get("entry_price") or 0) * (p.get("quantity") or 0)
            close_notional = (p.get("close_price") or 0) * (p.get("quantity") or 0)
            p["total_fees"] = round(calc_trade_fee(entry_notional) + calc_trade_fee(close_notional), 6)
        if p.get("margin_cost") is None:
            entry_notional = (p.get("entry_price") or 0) * (p.get("quantity") or 0)
            lev = p.get("leverage") or 1
            p["margin_cost"] = round(calc_margin_cost(entry_notional, lev, p.get("opened_at"), p.get("closed_at")), 6)

    # Build a dedup key set from real rows (ticker + minute-level timestamp)
    def _min(ts: str) -> str:
        return (ts or "")[:16]

    real_keys = {(_min(p.get("closed_at", "")), p["ticker"]) for p in real}

    # 1. All executed sells/covers from agent_log
    sell_r = (
        get_client().table("agent_log")
        .select("id,ticker,action,ts,position_side")
        .in_("action", ["sell", "cover"])
        .eq("executed", True)
        .order("ts", desc=True)
        .limit(limit)
        .execute()
    )
    sell_rows = sell_r.data or []

    # Orphan sells = agent_log sells with no matching real positions row
    orphans = [s for s in sell_rows if (_min(s.get("ts", "")), s["ticker"]) not in real_keys]

    if not orphans:
        return real

    tickers  = list({s["ticker"] for s in orphans})
    sell_ids = [s["id"] for s in orphans]

    # 2. trades rows for those sells (close price / qty)
    sell_tx_r = (
        get_client().table("trades")
        .select("agent_log_id,price,quantity")
        .in_("agent_log_id", sell_ids)
        .execute()
    )
    sell_tx_by_id = {row["agent_log_id"]: row for row in (sell_tx_r.data or [])}

    # 3. All executed buys/shorts for those tickers (to find entry prices)
    buy_r = (
        get_client().table("agent_log")
        .select("id,ticker,ts")
        .in_("action", ["buy", "short"])
        .eq("executed", True)
        .in_("ticker", tickers)
        .order("ts", desc=True)
        .limit(200)
        .execute()
    )
    buy_rows = buy_r.data or []

    # 4. trades for those buys (entry price)
    buy_ids = [b["id"] for b in buy_rows]
    buy_tx_by_id: dict = {}
    if buy_ids:
        buy_tx_r = (
            get_client().table("trades")
            .select("agent_log_id,price")
            .in_("agent_log_id", buy_ids)
            .execute()
        )
        buy_tx_by_id = {row["agent_log_id"]: row for row in (buy_tx_r.data or [])}

    # Group buys by ticker (desc order — [0] is most recent)
    from collections import defaultdict
    buys_by_ticker: dict = defaultdict(list)
    for b in buy_rows:
        buys_by_ticker[b["ticker"]].append(b)

    # Track which buy ids have already been claimed by a sell so no two
    # orphan sells share the same entry (prevents double-counting P&L).
    _claimed_buy_ids: set = set()

    # 5. Synthesize a positions-like dict for each orphan sell
    synthetic = []
    for sell in orphans:
        ticker     = sell["ticker"]
        close_ts   = sell.get("ts")
        side       = "long" if sell.get("position_side") == "long" else "short"

        sell_tx    = sell_tx_by_id.get(sell["id"], {})
        close_price = float(sell_tx.get("price") or 0) or None
        quantity    = float(sell_tx.get("quantity") or 0) or None

        # Find the last executed buy that occurred before this sell
        # and hasn't already been claimed by another orphan sell.
        entry_price = None
        opened_at   = None
        for buy in buys_by_ticker.get(ticker, []):
            if buy["id"] in _claimed_buy_ids:
                continue
            if (buy.get("ts") or "") < (close_ts or ""):
                buy_tx      = buy_tx_by_id.get(buy["id"], {})
                entry_price = float(buy_tx.get("price") or 0) or None
                opened_at   = buy.get("ts")
                _claimed_buy_ids.add(buy["id"])
                break

        pnl = None
        total_fees  = 0.0
        if entry_price and close_price and quantity:
            entry_notional = entry_price * quantity
            close_notional = close_price * quantity
            fee_in     = calc_trade_fee(entry_notional)
            fee_out    = calc_trade_fee(close_notional)
            total_fees = round(fee_in + fee_out, 6)
            raw_pnl = (
                (close_price - entry_price) * quantity if side == "long"
                else (entry_price - close_price) * quantity
            )
            pnl = round(raw_pnl - fee_in - fee_out, 2)

        synthetic.append({
            "id":            None,
            "ticker":        ticker,
            "side":          side,
            "quantity":      quantity,
            "entry_price":   entry_price,
            "close_price":   close_price,
            "realized_pnl":  pnl,
            "opened_at":     opened_at,
            "closed_at":     close_ts,
            "close_reason":  "ai_signal",
            "leverage":      1,
            "agent_log_id":  None,
            "stop_loss":     None,
            "total_fees":    total_fees,
            "margin_cost":   0.0,
        })

    merged = real + synthetic
    merged.sort(key=lambda x: x.get("closed_at") or "", reverse=True)
    return merged[:limit]


def get_realized_pnl() -> float:
    """Sum of realized P&L from all closed positions (real rows + synthetic orphans)."""
    closed = get_closed_positions_full(limit=10000)
    return sum((p["realized_pnl"] or 0) for p in closed if p.get("realized_pnl") is not None)


# ---------------------------------------------------------------------------
# Bot settings (single-row config table, id=1)
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS: dict = {
    "paper_capital":          1000.0,   # starting / total paper capital ($)
    "max_position_pct":       20.0,     # max % of capital per individual position
    "max_position_usd":       None,     # hard USD cap per position (None = use pct only)
    "max_leverage":           3,        # hard cap on leverage (1–5)
    "max_open_positions":     10,       # max concurrent open positions
    "risk_per_trade_pct":     2.0,      # max % of capital risked (for stop-loss sizing)
    "stop_loss_pct_default":  2.5,      # fallback stop-loss % if AI returns none
    "trailing_stop_atr_mult": 2.0,      # trailing stop distance = N * ATR from high-water
    "poll_interval_sec":      300,      # fast loop interval (seconds)
    "ai_timer_min":           60,       # force AI run every N minutes per ticker
    "cooldown_min":           30,       # per-flag cooldown after AI triggers (minutes)
}


def get_settings() -> dict:
    """Load bot settings from the bot_settings table. Falls back to defaults on error."""
    try:
        r = get_client().table("bot_settings").select("*").eq("id", 1).execute()
        if r.data:
            row = r.data[0]
            return {k: (row[k] if row.get(k) is not None else v)
                    for k, v in _DEFAULT_SETTINGS.items()}
    except Exception:
        pass
    return dict(_DEFAULT_SETTINGS)


def enrich_position(position: dict, current_price: float) -> dict:
    """Add unrealized P&L and time-in-trade fields to an open position dict."""
    ep   = position.get("entry_price") or 0
    side = position.get("side", "long")
    qty  = position.get("quantity") or 0
    lev  = position.get("leverage") or 1
    raw_usd        = ((current_price - ep) * qty * lev) if side == "long" else ((ep - current_price) * qty * lev)
    accrued_margin = calc_margin_cost(ep * qty, lev, position.get("opened_at"))
    net_usd        = round(raw_usd - accrued_margin, 2)
    margin_capital = ep * qty
    signed_pct     = round(net_usd / margin_capital * 100, 2) if margin_capital else 0.0
    opened_at_str  = position.get("opened_at")
    hrs_open       = None
    if opened_at_str:
        try:
            opened_dt = datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
            if opened_dt.tzinfo is None:
                opened_dt = opened_dt.replace(tzinfo=timezone.utc)
            hrs_open = round((datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600, 1)
        except Exception:
            pass
    return {
        **position,
        "unrealized_pnl_pct": signed_pct,
        "unrealized_pnl_usd": net_usd,
        "time_in_trade_hrs":  hrs_open,
    }


def build_portfolio_summary(
    paper_capital: float,
    all_open_positions: list[dict],
    realized_pnl: float,
    current_ticker: str | None = None,
    current_price: float | None = None,
) -> dict:
    """Compute a portfolio summary dict used by the AI pipeline and dashboard.

    current_ticker / current_price: when provided, the unrealized P&L for that
    ticker uses the live price instead of entry_price (which would contribute 0).
    """
    used_margin = sum(
        ((p.get("entry_price") or 0) * (p.get("quantity") or 0)) / max(p.get("leverage") or 1, 1)
        for p in all_open_positions
    )
    available_cash = paper_capital - used_margin + realized_pnl

    unrealized_pnl = 0.0
    for p in all_open_positions:
        ep  = p.get("entry_price") or 0
        qty = p.get("quantity") or 0
        lev = p.get("leverage") or 1
        side = p.get("side", "long")
        if not ep:
            continue
        cp = current_price if (current_ticker and p.get("ticker") == current_ticker) else ep
        raw = ((cp - ep) * qty * lev) if side == "long" else ((ep - cp) * qty * lev)
        accrued = calc_margin_cost(ep * qty, lev, p.get("opened_at"))
        unrealized_pnl += raw - accrued

    account_equity = paper_capital + realized_pnl + unrealized_pnl
    drawdown_pct = round((account_equity - paper_capital) / paper_capital * 100, 2) if paper_capital else 0.0

    return {
        "starting_capital":    paper_capital,
        "realized_pnl":        round(realized_pnl, 2),
        "unrealized_pnl":      round(unrealized_pnl, 2),
        "account_equity":      round(account_equity, 2),
        "open_position_count": len(all_open_positions),
        "available_cash":      round(available_cash, 2),
        "drawdown_pct":        drawdown_pct,
    }


def update_settings(updates: dict) -> dict:
    """Persist a partial or full settings update. Returns the full updated settings dict."""
    allowed = {k: v for k, v in updates.items() if k in _DEFAULT_SETTINGS}
    allowed["id"] = 1
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        get_client().table("bot_settings").upsert(allowed, on_conflict="id").execute()
    except Exception:
        # Fallback: some columns may not exist in DB yet — update only known ones
        row = get_client().table("bot_settings").select("*").eq("id", 1).execute()
        if row.data:
            db_cols = set(row.data[0].keys())
            safe = {k: v for k, v in allowed.items() if k in db_cols}
            get_client().table("bot_settings").upsert(safe, on_conflict="id").execute()
        else:
            raise
    return get_settings()
