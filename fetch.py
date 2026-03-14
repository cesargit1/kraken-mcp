"""
fetch.py — Incremental candle fetching from Kraken CLI and Yahoo Finance.
Fetches only new candles since the last stored timestamp.
"""

from datetime import datetime, timezone
from typing import Optional

from core import run_kraken
import db

# Kraken OHLC interval in minutes
TIMEFRAME_INTERVAL = {"1h": 60, "4h": 240, "1d": 1440, "1w": 10080}

# How many candles to read back for indicator computation
CANDLE_WINDOW = {"1h": 50, "4h": 50, "1d": 100, "1w": 100}


# ---------------------------------------------------------------------------
# Kraken
# ---------------------------------------------------------------------------

def fetch_kraken_candles(pair: str, ticker: str, timeframe: str, asset_class: str = "spot", since: Optional[int] = None) -> list[dict]:
    """Fetch OHLCV from Kraken CLI. Returns list of candle dicts."""
    interval = TIMEFRAME_INTERVAL[timeframe]
    args = ["ohlc", pair, "--interval", str(interval)]
    if since:
        args += ["--since", str(since)]
    if asset_class and asset_class != "spot":
        args += ["--asset-class", asset_class]

    result = run_kraken(args)
    if "error" in result:
        print(f"  [fetch] Kraken error {pair} {timeframe}: {result['error']}")
        return []

    # Response: { "PAIR": [[time, open, high, low, close, vwap, volume, count], ...], "last": N }
    raw = None
    for key, val in result.items():
        if key != "last" and isinstance(val, list):
            raw = val
            break

    if not raw:
        return []

    rows = []
    for c in raw:
        ts = datetime.fromtimestamp(int(c[0]), tz=timezone.utc).isoformat()
        rows.append({
            "ticker": ticker,
            "timeframe": timeframe,
            "ts": ts,
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[6]),
        })
    return rows


# ---------------------------------------------------------------------------
# Yahoo Finance (fallback for non-Kraken tickers)
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
    for ts, row in hist.iterrows():
        ts_str = ts.isoformat()
        if since_ts and ts_str <= since_ts:
            continue
        rows.append({
            "ticker": symbol,
            "timeframe": timeframe,
            "ts": ts_str,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        })
    return rows


# ---------------------------------------------------------------------------
# Incremental update (called by fast loop)
# ---------------------------------------------------------------------------

def update_candles(ticker_row: dict) -> None:
    """Fetch and store new candles at all 4 timeframes for a single ticker."""
    ticker      = ticker_row["ticker"]
    pair        = ticker_row.get("pair") or ticker
    source      = ticker_row.get("source", "kraken_xstock")
    asset_class = ticker_row.get("asset_class", "spot")

    for timeframe in TIMEFRAME_INTERVAL:
        latest_ts = db.get_latest_candle_ts(ticker, timeframe)
        since_unix = None
        if latest_ts:
            dt = datetime.fromisoformat(latest_ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            since_unix = int(dt.timestamp())

        if source.startswith("kraken"):
            rows = fetch_kraken_candles(pair, ticker, timeframe, asset_class=asset_class, since=since_unix)
        else:
            rows = fetch_yahoo_candles(ticker, timeframe, since_ts=latest_ts)

        # Skip the last candle from Kraken (it's the current in-progress candle)
        if rows and source.startswith("kraken"):
            rows = rows[:-1]

        if rows:
            db.upsert_candles(rows)
            print(f"  [fetch] {ticker} {timeframe}: +{len(rows)} candles")
