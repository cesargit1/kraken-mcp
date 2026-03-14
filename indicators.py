"""
indicators.py — Technical indicator computation and threshold detection.
Uses pandas-ta for indicators, scipy for peak/trough detection.
All computation is pure Python — no AI calls.
"""

from typing import Optional

import numpy as np
import pandas as pd

import db

try:
    import pandas_ta as ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False
    print("[indicators] pandas_ta not installed — indicators disabled")

try:
    from scipy.signal import find_peaks
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("[indicators] scipy not installed — peak detection disabled")

TIMEFRAMES = ["1h", "4h", "1d", "1w"]
CANDLE_LIMIT = {"1h": 50, "4h": 50, "1d": 100, "1w": 100}


# ---------------------------------------------------------------------------
# DataFrame helpers
# ---------------------------------------------------------------------------

def _to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values("ts").set_index("ts")  # DatetimeIndex required by VWAP
    df = df.rename(columns={
        "open": "Open", "high": "High",
        "low": "Low",  "close": "Close", "volume": "Volume",
    })
    return df


def _col(df: pd.DataFrame, prefix: str) -> Optional[str]:
    """Find the first column that starts with a given prefix."""
    matches = [c for c in df.columns if c.startswith(prefix)]
    return matches[0] if matches else None


def _last(series: Optional[pd.Series]) -> Optional[float]:
    if series is None or series.empty:
        return None
    val = series.dropna()
    return float(val.iloc[-1]) if not val.empty else None


# ---------------------------------------------------------------------------
# Indicator computation
# ---------------------------------------------------------------------------

def compute_indicators(rows: list[dict]) -> Optional[dict]:
    """
    Compute all technical indicators for a window of candles.
    Returns a dict ready to upsert into the indicators table.
    Returns None if insufficient data.
    """
    if len(rows) < 20:
        return None

    df = _to_df(rows)
    result: dict = {
        "ticker":        rows[0]["ticker"],
        "timeframe":     rows[0]["timeframe"],
        "ts":            df.index[-1].isoformat(),
        "latest_open":   float(df["Open"].iloc[-1]),
        "latest_high":   float(df["High"].iloc[-1]),
        "latest_low":    float(df["Low"].iloc[-1]),
        "latest_close":  float(df["Close"].iloc[-1]),
        "latest_volume": float(df["Volume"].iloc[-1]),
    }

    if not TA_AVAILABLE:
        result["threshold_flags"] = []
        return result

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    vol   = df["Volume"]

    # RSI
    rsi = ta.rsi(close, length=14)
    result["rsi"] = _last(rsi)

    # MACD
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None:
        result["macd"]        = _last(macd_df[_col(macd_df, "MACD_")])
        result["macd_signal"] = _last(macd_df[_col(macd_df, "MACDs_")])
        result["macd_hist"]   = _last(macd_df[_col(macd_df, "MACDh_")])

    # Bollinger Bands
    bb = ta.bbands(close, length=20)
    if bb is not None:
        result["bb_upper"]  = _last(bb[_col(bb, "BBU_")])
        result["bb_middle"] = _last(bb[_col(bb, "BBM_")])
        result["bb_lower"]  = _last(bb[_col(bb, "BBL_")])

    # EMAs
    ema20 = ta.ema(close, length=20)
    ema50 = ta.ema(close, length=50) if len(df) >= 50 else None
    result["ema_20"] = _last(ema20)
    result["ema_50"] = _last(ema50)

    # OBV
    obv = ta.obv(close, vol)
    result["obv"] = _last(obv)

    # ATR
    atr = ta.atr(high, low, close, length=14)
    result["atr"] = _last(atr)

    # VWAP (intraday only)
    tf = rows[0]["timeframe"]
    if tf in ("1h", "4h"):
        try:
            vwap = ta.vwap(high, low, close, vol)
            result["vwap"] = _last(vwap)
        except Exception:
            result["vwap"] = None

    # Peak / trough detection
    if SCIPY_AVAILABLE and len(df) >= 10:
        closes = close.values.astype(float)
        prominence = float(np.std(closes) * 0.5)
        peaks,   _ = find_peaks( closes, prominence=prominence, distance=5)
        troughs, _ = find_peaks(-closes, prominence=prominence, distance=5)
        result["peaks_json"] = {
            "indices":    peaks.tolist(),
            "prices":     closes[peaks].tolist(),
            "timestamps": [str(df.index[i]) for i in peaks],
        }
        result["troughs_json"] = {
            "indices":    troughs.tolist(),
            "prices":     closes[troughs].tolist(),
            "timestamps": [str(df.index[i]) for i in troughs],
        }

    # Threshold flags (inline, reusing already-computed series)
    result["threshold_flags"] = _check_thresholds(result, df, macd_df, bb, ema20, ema50)

    return result


# ---------------------------------------------------------------------------
# Threshold detection
# ---------------------------------------------------------------------------

def _check_thresholds(
    ind: dict,
    df: pd.DataFrame,
    macd_df: Optional[pd.DataFrame],
    bb: Optional[pd.DataFrame],
    ema20: Optional[pd.Series],
    ema50: Optional[pd.Series],
) -> list[str]:
    flags = []
    close = df["Close"]

    # RSI extremes
    rsi = ind.get("rsi")
    if rsi is not None:
        if rsi < 30:
            flags.append("rsi_oversold")
        elif rsi > 70:
            flags.append("rsi_overbought")

    # MACD histogram crossover
    if macd_df is not None:
        hist_col = _col(macd_df, "MACDh_")
        if hist_col:
            hist = macd_df[hist_col].dropna()
            if len(hist) >= 2:
                prev, curr = float(hist.iloc[-2]), float(hist.iloc[-1])
                if prev < 0 and curr > 0:
                    flags.append("macd_bullish_cross")
                elif prev > 0 and curr < 0:
                    flags.append("macd_bearish_cross")

    # Bollinger Band breakout / squeeze
    if bb is not None:
        bbu_col = _col(bb, "BBU_")
        bbl_col = _col(bb, "BBL_")
        bbm_col = _col(bb, "BBM_")
        if bbu_col and bbl_col and bbm_col:
            bbu = bb[bbu_col].dropna()
            bbl = bb[bbl_col].dropna()
            bbm = bb[bbm_col].dropna()
            if not bbu.empty:
                curr_close = float(close.iloc[-1])
                upper = float(bbu.iloc[-1])
                lower = float(bbl.iloc[-1])
                middle = float(bbm.iloc[-1])
                if curr_close > upper:
                    flags.append("bb_breakout_upper")
                elif curr_close < lower:
                    flags.append("bb_breakout_lower")
                if middle > 0 and (upper - lower) / middle < 0.04:
                    flags.append("bb_squeeze")

    # EMA golden / death cross
    if ema20 is not None and ema50 is not None:
        e20 = ema20.dropna()
        e50 = ema50.dropna()
        if len(e20) >= 2 and len(e50) >= 2:
            if float(e20.iloc[-2]) < float(e50.iloc[-2]) and float(e20.iloc[-1]) > float(e50.iloc[-1]):
                flags.append("ema_golden_cross")
            elif float(e20.iloc[-2]) > float(e50.iloc[-2]) and float(e20.iloc[-1]) < float(e50.iloc[-1]):
                flags.append("ema_death_cross")

    # Price spike (>2% move in latest candle)
    if len(close) >= 2:
        prev_c = float(close.iloc[-2])
        curr_c = float(close.iloc[-1])
        if prev_c > 0:
            pct = (curr_c - prev_c) / prev_c * 100
            if abs(pct) > 2.0:
                direction = "+" if pct > 0 else "-"
                flags.append(f"price_spike_{direction}{abs(pct):.1f}pct")

    return flags


# ---------------------------------------------------------------------------
# Multi-timeframe runner (called by fast loop)
# ---------------------------------------------------------------------------

def compute_all_timeframes(ticker_row: dict) -> dict[str, dict]:
    """
    Compute indicators for all 4 timeframes. Reads from DB, writes results back.
    Returns {timeframe: indicator_snapshot}.
    """
    ticker = ticker_row["ticker"]
    results = {}

    for tf in TIMEFRAMES:
        rows = db.get_candle_window(ticker, tf, limit=CANDLE_LIMIT[tf])
        if not rows:
            continue
        ind = compute_indicators(rows)
        if ind:
            db.upsert_indicators(ind)
            results[tf] = ind

    return results


def any_flags(all_indicators: dict[str, dict]) -> list[str]:
    """Collect all threshold flags across timeframes, prefixed with timeframe."""
    flags = []
    for tf, ind in all_indicators.items():
        for flag in ind.get("threshold_flags") or []:
            flags.append(f"{tf}:{flag}")
    return flags
