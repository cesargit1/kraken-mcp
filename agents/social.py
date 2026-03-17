"""
agents/social.py — Social sentiment specialist.
Fetches its own X data, then returns structured JSON sentiment signal.
Does NOT see: price charts, RSI, MACD, technical patterns, or holdings.
"""

import asyncio
from core import run_analyst_async, search_x, build_x_query

SYSTEM = """You are a social media sentiment analyst for a trading system. You analyze X (Twitter) posts and on-balance volume data to detect sentiment and social momentum.

Your job:
1. Assess the overall sentiment across recent X posts: bullish, bearish, or neutral
2. Distinguish genuine market interest from hype, coordinated shilling, and pump-and-dump patterns
   - Real interest: diverse accounts, factual discussion, analyst commentary, earnings reactions
   - Hype: repetitive messaging, influencer pumping, unusually high post volume with thin substance
3. Detect viral momentum — explosive early growth in mentions that precedes price moves
4. Cross-reference with OBV: if social sentiment is bullish but OBV is falling, real buyers aren't following — flag the divergence
5. Rate the overall signal strength

You do NOT see: price charts, RSI, MACD, Bollinger Bands, chart patterns, or portfolio holdings.

Respond with ONLY a valid JSON object — no markdown, no explanation outside the JSON:
{
  "signal": "bullish" | "bearish" | "neutral",
  "confidence": <integer 0-100>,
  "sentiment_score": <integer -100 to 100, negative=bearish, positive=bullish>,
  "hype_vs_real": "real" | "hype" | "mixed",
  "viral_detected": <true | false>,
  "obv_confirmation": <true | false | null>,
  "notable_themes": ["<key topic 1>", "<key topic 2>"],
  "reasoning": "<2-3 sentences on what the social data shows>",
  "risk_factors": ["<factor 1>", "<factor 2>"]
}"""


async def analyze(context: dict) -> dict:
    """
    context keys:
      ticker_row - full watchlist row (for building the X query)
      obv        - {timeframe: float} — OBV values across timeframes
    """
    ticker_row = context["ticker_row"]
    obv        = context.get("obv", {})

    # Fetch X posts directly — social agent owns this data, not the orchestrator
    loop = asyncio.get_event_loop()
    x_query = build_x_query(ticker_row)
    x_posts = await loop.run_in_executor(None, lambda: search_x(x_query))

    llm_context = {
        "ticker":  ticker_row["ticker"],
        "x_posts": x_posts,
        "obv":     obv,
    }
    return await run_analyst_async(SYSTEM, llm_context)
