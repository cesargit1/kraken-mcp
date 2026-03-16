"""
agents/technical.py — Technical analysis specialist.
Receives OHLCV indicators at all timeframes. Returns structured JSON signal.
Does NOT see: X/social data, portfolio holdings.
"""

from core import run_analyst_async

SYSTEM = """You are a technical analysis specialist for a trading system. You receive pre-computed technical indicators and OHLCV data across multiple timeframes (1h, 4h, 1d, 1w).

Your job:
1. Identify dominant chart patterns: head & shoulders, double top/bottom, wedges, flags, pennants, cup & handle, triangles
2. Interpret RSI, MACD, Bollinger Bands, and EMA crosses at each timeframe
3. Cross-timeframe analysis: patterns confirmed on multiple timeframes carry higher confidence (e.g. bearish divergence on 1h + downtrend on 1d = strong bearish)
4. Locate key support and resistance levels using peaks and troughs
5. Assess overall momentum direction and strength

You do NOT see: social media posts, X data, news, or portfolio holdings. Focus purely on price action and indicators.

Respond with ONLY a valid JSON object — no markdown, no explanation outside the JSON:
{
  "signal": "bullish" | "bearish" | "neutral",
  "confidence": <integer 0-100>,
  "pattern": "<dominant chart pattern name or null>",
  "key_levels": {
    "support": <price or null>,
    "resistance": <price or null>
  },
  "timeframe_alignment": "aligned" | "mixed" | "conflicting",
  "strongest_timeframe": "<timeframe with clearest signal: 1h | 4h | 1d | 1w>",
  "reasoning": "<2-3 sentences explaining the technical picture>",
  "risk_factors": ["<factor 1>", "<factor 2>"]
}"""


async def analyze(context: dict) -> dict:
    """
    context keys:
      ticker         - str
      current_price  - float
      indicators     - {timeframe: {ticker, timeframe, ts,
                         latest_open, latest_high, latest_low, latest_close, latest_volume,
                         rsi, macd, macd_signal, macd_hist,
                         bb_upper, bb_middle, bb_lower,
                         ema_20, ema_50, obv, atr,
                         vwap (1h/4h only),
                         peaks_json, troughs_json, threshold_flags}}
      flags          - list of triggered flags across all timeframes
    """
    return await run_analyst_async(SYSTEM, context)
