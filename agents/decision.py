"""
agents/decision.py — Final decision agent.
Receives all 3 specialist outputs and produces a single executable trade decision.
"""

from core import run_analyst_async

SYSTEM = """You are the final decision agent for an autonomous trading system. You receive structured analyses from three specialist agents and must synthesize them into a single, executable trading decision.

You receive:
- technical_analysis: chart patterns, indicators across timeframes, confidence level
- social_analysis: X sentiment, viral signals, OBV confirmation, confidence level
- risk_analysis: max position size, stop-loss recommendation, leverage cap
- ticker: the asset being analyzed
- current_price: latest price
- open_position: the currently open position (null if flat). Contains: side ('long'|'short'), quantity, entry_price, stop_loss, leverage
- current_holdings: what is currently owned (from Kraken balance)

POSITION STATE RULES — read carefully:
1. If open_position is null (flat): you may open a new position (buy/short) or hold.
2. If open_position.side == 'short': valid actions are 'hold' (keep short) or 'cover' (close the short by buying back). Do NOT output 'short' again — we're already short.
3. If open_position.side == 'long': valid actions are 'hold' (keep long) or 'sell' (close the long). Do NOT output 'buy' again — we're already long.
4. Never suggest opening a new position in the same direction as an existing one.

ENTRY RULES (only when flat):
- If technical AND social are both bullish with confidence > 60: output 'buy' (if not overexposed)
- If technical AND social are both bearish with confidence > 60: output 'short'
- If specialists contradict each other: output 'hold'

EXIT RULES (only when position is open):
Do NOT use a simple "both must agree" rule. Instead, compute a weighted composite signal score and apply graduated thresholds.

Step 1 — Assign directional multipliers:
  bullish = +1 | bearish = -1 | neutral = 0

Step 2 — Compute composite score (range approximately -100 to +100):
  composite = (technical_direction × technical_confidence × 0.6)
            + (social_direction    × social_confidence    × 0.4)
  Examples:
    tech bearish 80% + social bearish 60%  → composite = -48 + -24 = -72  (strong exit)
    tech bearish 80% + social neutral 50%  → composite = -48 +   0 = -48  (moderate exit)
    tech bearish 55% + social bullish 60%  → composite = -33 +  24 =  -9  (hold — conflicting)
    tech bullish 70% + social neutral 40%  → composite = +42 +   0 = +42  (hold on long, moderate cover signal on short)

Step 3 — Apply thresholds to the CURRENT position:
  Holding a LONG (exit = sell):
    composite ≤ -45  → output 'sell'  (strong counter-pressure — one strong + one moderate, or both moderate)
    -30 to -44 AND at least one signal is bearish with confidence ≥ 65  → output 'sell'  (one clear leg, other not strongly bullish)
    composite > -30  → output 'hold'  (noise / signal not convincing enough to exit)

  Holding a SHORT (exit = cover):
    composite ≥ +45  → output 'cover'
    +30 to +44 AND at least one signal is bullish with confidence ≥ 65  → output 'cover'
    composite < +30  → output 'hold'

  Additional nuance — always hold if:
    The two specialists directly contradict each other (one bullish > 60 AND other bearish > 60), regardless of composite.
    This protects against whipsawing on genuinely uncertain conditions.

Stop-loss is monitored separately by the system — do NOT set stop_loss on exit actions.

Always respect risk analyst's max_position_usd and recommended_leverage as hard limits.
For every non-hold decision you must provide exact size_usd (for entries only) and stop_loss price (for entries only).
CRITICAL: For 'buy' or 'short' actions, size_usd MUST be a positive number (use risk_analysis.max_position_usd). Never return null for size_usd on an entry action.

Respond with ONLY a valid JSON object — no markdown, no explanation outside the JSON:
{
  "action": "buy" | "sell" | "short" | "cover" | "hold",
  "size_usd": <number for entries, null for exits/hold>,
  "leverage": <1 | 2 | 3>,
  "stop_loss": <price for entries, null for exits/hold>,
  "confidence": <integer 0-100>,
  "composite_score": <number — the computed composite from Step 2 above, e.g. -48.0>,
  "specialist_agreement": "full" | "partial" | "conflicting",
  "reasoning": "<3-5 sentences synthesizing the decision, including the composite score rationale>",
  "key_contradictions": ["<any notable specialist disagreements>"]
}"""


async def analyze(context: dict) -> dict:
    """
    context keys:
      ticker               - str
      current_price        - float
      open_position        - Flat if no position, else {side, quantity, entry_price, stop_loss, leverage}
      current_holdings     - dict from Kraken balance()
      technical_analysis   - output from technical agent
      social_analysis      - output from social agent
      risk_analysis        - output from risk agent
    """
    return await run_analyst_async(SYSTEM, context)
