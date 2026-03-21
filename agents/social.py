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
    context keys — two supported calling conventions:

    Bot flow (social agent fetches X itself):
      ticker_row - full watchlist row {ticker, asset_class, search_name, ...}
      obv        - {timeframe: float} — OBV values across timeframes

    UI flow (X already fetched by the caller):
      ticker     - str
      x_posts    - str — pre-fetched X post text
      obv        - {timeframe: float} — OBV values across timeframes
    """
    obv = context.get("obv", {})

    if "x_posts" in context:
        # X data already provided (e.g. pre-fetched by caller)
        llm_context = {
            "ticker":  context["ticker"],
            "x_posts": context["x_posts"],
            "obv":     obv,
        }
    elif "x_search_query" in context:
        # Query string provided — call search_x directly
        ticker = context["ticker"]
        query  = context["x_search_query"]
        loop = asyncio.get_event_loop()
        print(f"  [social] Searching X for {ticker}...")
        x_posts = await loop.run_in_executor(None, lambda: search_x(query))
        print(f"  [social] X posts received for {ticker} ({len(x_posts)} chars)")
        llm_context = {
            "ticker":  ticker,
            "x_posts": x_posts,
            "obv":     obv,
        }
    else:
        # Bot flow: full ticker_row provided, build query from it
        ticker_row = context["ticker_row"]
        loop = asyncio.get_event_loop()
        x_query = build_x_query(ticker_row)
        ticker = ticker_row["ticker"]
        print(f"  [social] Searching X for {ticker}...")
        x_posts = await loop.run_in_executor(None, lambda: search_x(x_query))
        print(f"  [social] X posts received for {ticker} ({len(x_posts)} chars)")
        llm_context = {
            "ticker":  ticker,
            "x_posts": x_posts,
            "obv":     obv,
        }

    return await run_analyst_async(SYSTEM, llm_context)
