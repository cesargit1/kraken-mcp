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
# Trade log
# ---------------------------------------------------------------------------

def log_trade(
    ticker: str,
    action: str,
    size: float,
    leverage: int,
    stop_loss: float,
    entry_price: float,
    technical: dict,
    social: dict,
    risk: dict,
    decision_reasoning: str,
    executed: bool = False,
    execution_result: Optional[dict] = None,
) -> int:
    r = (
        get_client().table("trade_log")
        .insert(
            {
                "ticker": ticker,
                "action": action,
                "size": size,
                "leverage": leverage,
                "stop_loss": stop_loss,
                "entry_price": entry_price,
                "technical_analysis": technical,
                "social_analysis": social,
                "risk_analysis": risk,
                "decision_reasoning": decision_reasoning,
                "executed": executed,
                "execution_result": execution_result,
            }
        )
        .execute()
    )
    return r.data[0]["id"]


def mark_trade_executed(trade_id: int, execution_result: dict) -> None:
    get_client().table("trade_log").update(
        {"executed": True, "execution_result": execution_result}
    ).eq("id", trade_id).execute()


def get_recent_trades(limit: int = 100) -> list[dict]:
    r = (
        get_client().table("trade_log")
        .select("id,ticker,action,size,leverage,stop_loss,entry_price,decision_reasoning,executed,execution_result,ts")
        .order("ts", desc=True)
        .limit(limit)
        .execute()
    )
    return r.data or []


def get_trade_by_id(trade_id: int) -> dict | None:
    """Return a single trade_log row with all columns including specialist analysis blobs."""
    r = (
        get_client().table("trade_log")
        .select("*")
        .eq("id", trade_id)
        .limit(1)
        .execute()
    )
    return r.data[0] if r.data else None


def get_all_watchlist_tickers() -> list[dict]:
    """Return all watchlist rows including inactive ones."""
    r = get_client().table("watchlist").select("*").execute()
    return r.data or []


# ---------------------------------------------------------------------------
# Positions (open position tracking — one row per ticker, upserted on open)
# ---------------------------------------------------------------------------

def open_position(
    ticker: str,
    side: str,
    volume: float,
    entry_price: float,
    stop_loss: Optional[float],
    leverage: int,
    trade_log_id: int,
) -> None:
    """Insert a new open position, or merge into an existing one (volume-weighted avg entry)."""
    existing = get_open_position(ticker)
    if existing and existing.get("side") == side:
        # Merge: volume-weighted average entry price, summed volume
        old_vol   = existing["volume"]
        old_entry = existing["entry_price"]
        new_vol   = old_vol + volume
        new_entry = round((old_entry * old_vol + entry_price * volume) / new_vol, 6)
        get_client().table("positions").update(
            {
                "volume":       new_vol,
                "entry_price":  new_entry,
                "stop_loss":    stop_loss or existing.get("stop_loss"),
                "trade_log_id": trade_log_id,  # latest trade id
            }
        ).eq("ticker", ticker).is_("closed_at", "null").execute()
    else:
        get_client().table("positions").upsert(
            {
                "ticker":       ticker,
                "side":         side,
                "volume":       volume,
                "entry_price":  entry_price,
                "stop_loss":    stop_loss,
                "leverage":     leverage,
                "trade_log_id": trade_log_id,
                "opened_at":    datetime.now(timezone.utc).isoformat(),
                "closed_at":    None,
                "close_price":  None,
                "pnl":          None,
                "close_reason": None,
            },
            on_conflict="ticker",
        ).execute()


def close_position(ticker: str, close_price: float, close_reason: str) -> Optional[dict]:
    """Mark the open position for a ticker as closed. Returns the closed row."""
    pos = get_open_position(ticker)
    if not pos:
        return None
    volume = pos["volume"]
    entry  = pos["entry_price"]
    lev    = pos["leverage"]
    pnl = (
        (close_price - entry) * volume * lev if pos["side"] == "long"
        else (entry - close_price) * volume * lev
    )
    now = datetime.now(timezone.utc).isoformat()
    get_client().table("positions").update({
        "closed_at":    now,
        "close_price":  close_price,
        "pnl":          round(pnl, 2),
        "close_reason": close_reason,
    }).eq("ticker", ticker).is_("closed_at", "null").execute()
    return {**pos, "closed_at": now, "close_price": close_price, "pnl": round(pnl, 2)}


def get_open_position(ticker: str) -> Optional[dict]:
    r = (
        get_client().table("positions")
        .select("*")
        .eq("ticker", ticker)
        .is_("closed_at", "null")
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


def get_realized_pnl() -> float:
    """Sum of P&L from all closed positions."""
    r = (
        get_client().table("positions")
        .select("pnl")
        .not_.is_("closed_at", "null")
        .execute()
    )
    return sum((row["pnl"] or 0) for row in (r.data or []))


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
    get_client().table("bot_settings").upsert(allowed, on_conflict="id").execute()
    return get_settings()
