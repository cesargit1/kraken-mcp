"""
fetch.py — Incremental candle fetching via Yahoo Finance.
Fetches only new candles since the last stored timestamp.
"""

import math
from typing import Optional

import db

# Timeframe interval in minutes (used for period/interval mapping only)
TIMEFRAME_INTERVAL = {"1h": 60, "4h": 240, "1d": 1440, "1w": 10080}

# How many candles to read back for indicator computation
CANDLE_WINDOW = {"1h": 50, "4h": 50, "1d": 100, "1w": 100}


# ---------------------------------------------------------------------------
# Yahoo Finance
# ---------------------------------------------------------------------------

def fetch_yahoo_candles(symbol: str, timeframe: str, since_ts: Optional[str] = None) -> list[dict]:
    """Fetch OHLCV from Yahoo Finance. Returns list of candle dicts."""
    try:
        import yfinance as yf
    except ImportError:
        print("  [fetch] yfinance not installed — skipping Yahoo fetch")
        return []

    yf_interval = {"1h": "1h", "4h": "1h", "1d": "1d", "1w": "1wk"}
    yf_period   = {"1h": "7d", "4h": "30d", "1d": "6mo", "1w": "2y"}

    hist = yf.Ticker(symbol).history(
        period=yf_period[timeframe],
        interval=yf_interval[timeframe],
        auto_adjust=True,
    )

    if hist.empty:
        return []

    # Resample 1h -> 4h if needed
    if timeframe == "4h":
        hist = (
            hist.resample("4h")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
            .dropna()
        )

    rows = []
    skipped_rows = 0
    for ts, row in hist.iterrows():
        ts_str = ts.isoformat()
        if since_ts and ts_str <= since_ts:
            continue
        values = {
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        }
        if not all(math.isfinite(value) for value in values.values()):
            skipped_rows += 1
            continue
        rows.append({
            "ticker": symbol,
            "timeframe": timeframe,
            "ts": ts_str,
            **values,
        })
    if skipped_rows:
        print(f"  [fetch] {symbol} {timeframe}: skipped {skipped_rows} Yahoo rows with non-finite OHLCV")
    return rows


# ---------------------------------------------------------------------------
# Incremental update (called by fast loop)
# ---------------------------------------------------------------------------

def update_candles(ticker_row: dict) -> None:
    """Fetch and store new candles at all 4 timeframes for a single ticker."""
    ticker = ticker_row["ticker"]

    for timeframe in TIMEFRAME_INTERVAL:
        latest_ts = db.get_latest_candle_ts(ticker, timeframe)
        rows = fetch_yahoo_candles(ticker, timeframe, since_ts=latest_ts)

        if rows:
            db.upsert_candles(rows)
            print(f"  [fetch] {ticker} {timeframe}: +{len(rows)} candles")
