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


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def get_watchlist() -> list[dict]:
    r = get_client().table("watchlist").select("*").eq("active", True).execute()
    return r.data


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------

def upsert_candles(rows: list[dict]) -> None:
    if rows:
        get_client().table("candles").upsert(rows, on_conflict="ticker,timeframe,ts").execute()


def get_candle_window(ticker: str, timeframe: str, limit: int = 100) -> list[dict]:
    """Return the most recent N candles in chronological order."""
    r = (
        get_client().table("candles")
        .select("*")
        .eq("ticker", ticker)
        .eq("timeframe", timeframe)
        .order("ts", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(r.data))


def get_latest_candle_ts(ticker: str, timeframe: str) -> Optional[str]:
    r = (
        get_client().table("candles")
        .select("ts")
        .eq("ticker", ticker)
        .eq("timeframe", timeframe)
        .order("ts", desc=True)
        .limit(1)
        .execute()
    )
    return r.data[0]["ts"] if r.data else None


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

_EPHEMERAL_INDICATOR_KEYS = {"latest_close", "latest_open", "latest_high", "latest_low", "latest_volume"}

def upsert_indicators(row: dict) -> None:
    db_row = {k: v for k, v in row.items() if k not in _EPHEMERAL_INDICATOR_KEYS}
    get_client().table("indicators").upsert(db_row, on_conflict="ticker,timeframe,ts").execute()


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
# Cooldown
# ---------------------------------------------------------------------------

def check_cooldown(ticker: str, event_type: str, window_minutes: int = 30) -> bool:
    """Return True if ticker+event_type was triggered within the cooldown window (should skip)."""
    r = (
        get_client().table("cooldown")
        .select("triggered_at")
        .eq("ticker", ticker)
        .eq("event_type", event_type)
        .order("triggered_at", desc=True)
        .limit(1)
        .execute()
    )
    if not r.data:
        return False
    last = datetime.fromisoformat(r.data[0]["triggered_at"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() < window_minutes * 60


def set_cooldown(ticker: str, event_type: str) -> None:
    get_client().table("cooldown").insert(
        {"ticker": ticker, "event_type": event_type}
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
    pair: Optional[str] = None,
    trigger_flags: Optional[str] = None,
    position_side: str = "flat",
    indicators_snapshot: Optional[dict] = None,
    executed: bool = False,
    decision_json: Optional[dict] = None,
) -> int:
    payload = {
        "ticker": ticker,
        "pair": pair,
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
        .select("id,ticker,pair,action,trigger_flags,decision_reasoning,position_side,executed,ts", count="exact")
        .order("ts", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return {"data": r.data or [], "total": r.count or 0}


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
    """Return all transaction_ledger rows linked to a given agent_log session."""
    r = (
        get_client().table("transaction_ledger")
        .select(
            "id,event_time,status,side,is_simulated,is_margin,"
            "base_asset,quote_asset,pair_symbol,"
            "quantity,price,gross_amount,gross_currency,"
            "fee_amount,fee_asset,net_amount,net_currency,"
            "cost,cost_currency,leverage,order_type,"
            "transaction_type,transaction_subtype,source_type,"
            "external_id,order_id,source_command"
        )
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
    stop_loss: Optional[float],
    fee: float,
    pair: Optional[str],
    order_type: str,
    source_type: str,
    is_simulated: bool,
    execution_result: Optional[dict],
) -> None:
    """Record a single exchange transaction receipt linked to an agent_log session."""
    from datetime import datetime, timezone
    side = "buy" if action in ("buy", "cover") else "sell"
    tx_type = "trade"
    status = "failed" if (execution_result or {}).get("error") else "completed"
    get_client().table("transaction_ledger").insert(
        {
            "agent_log_id":      agent_log_id,
            "exchange":          "kraken",
            "transaction_type":  tx_type,
            "transaction_subtype": action,
            "source_type":       source_type,
            "external_id":       str((execution_result or {}).get("order_id") or f"sim-{agent_log_id}-{action}"),
            "status":            status,
            "side":              side,
            "is_simulated":      is_simulated,
            "is_margin":         leverage > 1,
            "event_time":        datetime.now(timezone.utc).isoformat(),
            "base_asset":        ticker,
            "quote_asset":       "USD",
            "pair_symbol":       pair or f"{ticker}/USD",
            "quantity":          str(volume) if volume else None,
            "price":             str(current_price) if current_price else None,
            "gross_amount":      str(notional_usd) if notional_usd else None,
            "gross_currency":    "USD",
            "fee_amount":        str(fee) if fee else None,
            "fee_asset":         "USD",
            "net_amount":        str(round(notional_usd - fee, 8)) if notional_usd else None,
            "net_currency":      "USD",
            "cost":              str(notional_usd) if notional_usd else None,
            "cost_currency":     "USD",
            "leverage":          str(leverage),
            "order_type":        order_type,
            "trigger_price":     str(stop_loss) if stop_loss else None,
            "raw_payload":       execution_result or {},
            "source_command":    "bot.execute_trade",
        }
    ).execute()


def get_recent_transactions(limit: int = 30) -> list[dict]:
    """Return recent transaction_ledger rows for the Recent Trades UI."""
    r = (
        get_client().table("transaction_ledger")
        .select(
            "id,agent_log_id,event_time,status,side,base_asset,pair_symbol,"
            "transaction_subtype,quantity,price,gross_amount,leverage,"
            "fee_amount,is_simulated,order_type,trigger_price"
        )
        .order("event_time", desc=True)
        .limit(limit)
        .execute()
    )
    return r.data or []


def get_all_watchlist_tickers() -> list[dict]:
    """Return all watchlist rows including inactive ones."""
    r = get_client().table("watchlist").select("*").execute()
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
    """Insert a new open position, or merge into an existing one (quantity-weighted avg entry)."""
    existing = get_open_position(ticker)
    if existing and existing.get("side") == side:
        # Merge: quantity-weighted average entry price, summed quantity
        old_qty   = existing["quantity"]
        old_entry = existing["entry_price"]
        new_qty   = old_qty + quantity
        new_entry = round((old_entry * old_qty + entry_price * quantity) / new_qty, 6)
        get_client().table("positions").update(
            {
                "quantity":      new_qty,
                "entry_price":   new_entry,
                "stop_loss":     stop_loss or existing.get("stop_loss"),
                "agent_log_id":  agent_log_id,  # latest agent run id
            }
        ).eq("ticker", ticker).is_("closed_at", "null").execute()
    else:
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
    lev      = pos["leverage"]
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
    get_client().table("positions").update({
        "closed_at":     now,
        "close_price":   close_price,
        "realized_pnl":  round(pnl, 2),
        "close_reason":  close_reason,
    }).eq("ticker", ticker).is_("closed_at", "null").execute()
    return {
        **pos,
        "closed_at":    now,
        "close_price":  close_price,
        "realized_pnl": round(pnl, 2),
        "total_fees":   round(fee_in + fee_out, 6),
        "margin_cost":  round(margin_c, 6),
    }


def get_open_position(ticker: str) -> Optional[dict]:
    r = (
        get_client().table("positions")
        .select("*")
        .eq("ticker", ticker)
        .is_("closed_at", "null")
        .order("opened_at", desc=False)   # oldest open row first (should only ever be one)
        .limit(1)
        .execute()
    )
    return r.data[0] if r.data else None


def get_all_open_positions() -> list[dict]:
    r = (
        get_client().table("positions")
        .select("*")
        .is_("closed_at", "null")
        .order("opened_at", desc=True)
        .execute()
    )
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
                   positions row — reconstructed using transaction_ledger prices.
    This ensures history is visible even when a positions row was lost.
    Returned list is sorted by closed_at desc, capped at `limit`.
    """
    real = get_closed_positions(limit)

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

    # 2. transaction_ledger rows for those sells (close price / qty)
    sell_tx_r = (
        get_client().table("transaction_ledger")
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

    # 4. transaction_ledger for those buys (entry price)
    buy_ids = [b["id"] for b in buy_rows]
    buy_tx_by_id: dict = {}
    if buy_ids:
        buy_tx_r = (
            get_client().table("transaction_ledger")
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
        entry_price = None
        opened_at   = None
        for buy in buys_by_ticker.get(ticker, []):
            if (buy.get("ts") or "") < (close_ts or ""):
                buy_tx      = buy_tx_by_id.get(buy["id"], {})
                entry_price = float(buy_tx.get("price") or 0) or None
                opened_at   = buy.get("ts")
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
