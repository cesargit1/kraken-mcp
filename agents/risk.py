"""
agents/risk.py — Risk management specialist.
Receives ATR, holdings, portfolio exposure, and risk settings. Returns position sizing and stop-loss.
Has NO directional opinion. Does NOT see: charts, sentiment, or news.
"""

from core import run_analyst_async

_SYSTEM_TEMPLATE = """You are a risk management specialist for a trading system. You have NO directional opinion on whether to buy or sell. Your only job is position sizing, stop-loss placement, and leverage management.

You receive:
- ATR (average true range) at multiple timeframes — this is your volatility measure
- Current portfolio holdings and USD balances
- Recent trade performance (wins/losses) if available
- The threshold flags that triggered this analysis cycle
- Risk settings (hard limits from the operator — you MUST NOT exceed these)

=== OPERATOR RISK LIMITS (HARD CONSTRAINTS) ===
{limits_block}
=== END LIMITS ===

Your job:
1. Recommend a maximum position size in USD — MUST NOT exceed operator limits above
   - Higher ATR = smaller position size
   - Overexposed to this ticker already = reduce or skip
   - Scale position as a % of available capital, not a fixed dollar amount
2. Recommend a stop-loss level as a percentage distance from entry
   - Typically 1.5x–2x the ATR as a buffer
   - Tighter stops for high-leverage trades
   - Default to {stop_loss_pct_default}% if ATR is unavailable
3. Recommend a leverage level — MUST NOT exceed max_leverage ({max_leverage}x)
   - Only recommend >1x if volatility is manageable and stop-loss is tight
   - Max {max_leverage}x on xStocks (Kraken limit)
4. Flag any concentration risk or overexposure issues

You do NOT see: X posts, chart patterns, RSI, MACD, or directional signals.

Respond with ONLY a valid JSON object — no markdown, no explanation outside the JSON:
{{
  "max_position_usd": <number — must be <= operator limit>,
  "stop_loss_pct": <number — percentage from entry, e.g. 2.5 means 2.5% away>,
  "recommended_leverage": <integer 1–{max_leverage}>,
  "exposure_ok": <true | false>,
  "risk_factors": ["<factor 1>", "<factor 2>"],
  "reasoning": "<2-3 sentences on sizing rationale>"
}}"""


def _build_system(settings: dict) -> str:
    cap         = settings.get("paper_capital", 1000)
    max_pct     = settings.get("max_position_pct", 20)
    max_usd_raw = settings.get("max_position_usd")
    max_lev     = settings.get("max_leverage", 3)
    max_pos     = settings.get("max_open_positions", 10)
    risk_pct    = settings.get("risk_per_trade_pct", 2)
    sl_default  = settings.get("stop_loss_pct_default", 2.5)

    max_usd = max_usd_raw if max_usd_raw else round(cap * max_pct / 100, 2)

    lines = [
        f"- Total paper capital:         ${cap:,.2f}",
        f"- Max position size:           ${max_usd:,.2f}  ({max_pct}% of capital)",
        f"- Max leverage:                {max_lev}x",
        f"- Max concurrent positions:    {max_pos}",
        f"- Max % of capital risked/trade: {risk_pct}%  (for stop-loss sizing)",
        f"- Default stop-loss %:         {sl_default}%",
    ]
    return _SYSTEM_TEMPLATE.format(
        limits_block="\n".join(lines),
        stop_loss_pct_default=sl_default,
        max_leverage=max_lev,
    )


async def analyze(context: dict) -> dict:
    """
    context keys:
      ticker         - str
      current_price  - float
      atr            - {timeframe: float} — ATR across timeframes
      holdings       - dict from Kraken balance()
      flags          - triggered threshold flags
      settings       - dict from db.get_settings() (optional, falls back to defaults)
    """
    import db as _db
    settings = context.get("settings") or _db.get_settings()
    system   = _build_system(settings)
    return await run_analyst_async(system, context)
