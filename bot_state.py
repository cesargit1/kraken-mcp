"""
bot_state.py — Shared in-process state between bot.py and ui_server.py.

Kept in a separate module to avoid circular imports
(ui_server imports bot, bot imports ui_server → partially-initialized module error).
"""

import time as _time

# ---------------------------------------------------------------------------
# Per-ticker pipeline state
# ---------------------------------------------------------------------------
_ticker_states: dict[str, dict] = {}


def update_ticker_state(ticker: str, **kwargs) -> None:
    existing = _ticker_states.get(ticker, {"ticker": ticker, "started_at": _time.time()})
    existing.update(kwargs)
    existing["updated_at"] = _time.time()
    _ticker_states[ticker] = existing


def get_ticker_states() -> dict:
    return _ticker_states


# ---------------------------------------------------------------------------
# Cycle-level timing
# ---------------------------------------------------------------------------
_cycle_state: dict = {
    "last_cycle_at": None,
    "next_cycle_at": None,
    "poll_interval_sec": 300,
}


def update_cycle_state(**kwargs) -> None:
    _cycle_state.update(kwargs)


def get_cycle_state() -> dict:
    return _cycle_state
