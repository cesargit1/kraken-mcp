"""
core.py — shared runtime for the paper trading bot.
Handles: Grok client (xAI API), X search, and LLM analyst helpers.
"""

import os
import re
import json
import asyncio
from openai import OpenAI
from dotenv import load_dotenv
from retry import retry_call

load_dotenv()


# ---------------------------------------------------------------------------
# Ticker helpers (used by bot.py and ui_server.py)
# ---------------------------------------------------------------------------

def get_search_name(ticker_row: dict) -> str:
    """Human-readable name for social media search queries.
    Uses the explicit search_name column if set, otherwise falls back to the ticker."""
    name = ticker_row.get("search_name")
    if name:
        return name
    return ticker_row["ticker"]


def build_x_query(ticker_row: dict) -> str:
    """Build an X search query appropriate to the asset type."""
    name = get_search_name(ticker_row)
    asset_class = ticker_row.get("asset_class", "stock")
    if asset_class == "crypto":
        return (
            f"Search X for posts about ${name} cryptocurrency in the last hour. "
            "Include post texts, volume trends, and any notable news or sentiment."
        )
    return (
        f"Search X for posts about ${name} stock in the last hour. "
        "Include post texts, volume trends, and any notable news or sentiment."
    )


# ---------------------------------------------------------------------------
# Grok client (lazy — created on first use so missing key only fails at
# AI call time, not at import/startup)
# ---------------------------------------------------------------------------

_client: OpenAI | None = None
MODEL = "grok-4.20-0309-reasoning"
_REQUEST_TIMEOUT = 300.0  # seconds — applies to all Responses API calls


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("XAI_API_KEY")
        if not api_key:
            raise RuntimeError("XAI_API_KEY environment variable is not set")
        # Default openai SDK timeout is 600s (10 min) — far too long for x_search
        _client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1", timeout=_REQUEST_TIMEOUT)
    return _client


# ---------------------------------------------------------------------------
# X Search helper (uses Responses API — separate from chat completions loop)
# ---------------------------------------------------------------------------

def search_x(query: str) -> str:
    """
    Search X (Twitter) posts in real time using Grok's native x_search tool.
    Returns a text summary. Use this to gather X sentiment before calling run_agent.
    """
    def _call():
        response = _get_client().responses.create(
            model=MODEL,
            input=[{"role": "user", "content": query}],
            tools=[{"type": "x_search"}],
        )
        return response.output_text
    return retry_call(_call, label="x_search")


def search_x_stream(query: str):
    """
    Streaming version of search_x. Yields text delta strings as they arrive.
    Caller should collect them; the final full text is the concatenation of all deltas.
    """
    with _get_client().responses.stream(
        model=MODEL,
        input=[{"role": "user", "content": query}],
        tools=[{"type": "x_search"}],
    ) as stream:
        for event in stream:
            # The streaming Responses API emits response.output_text.delta events
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", None)
                if delta:
                    yield delta


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

def run_analyst(system_prompt: str, context: dict, model: str = MODEL) -> dict:
    """
    Single structured LLM call for a specialist analyst.
    Returns parsed JSON dict. No tool calls — context is pre-built.
    Uses the Responses API (required by multi-agent models).
    Retries on API errors and JSON parse failures.
    """
    def _call():
        response = _get_client().responses.create(
            model=model,
            instructions=system_prompt,
            input=json.dumps(context, indent=2),
        )
        content = response.output_text
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r'\{[\s\S]*\}', content)
            if m:
                try:
                    return json.loads(m.group())
                except Exception:
                    pass
            return {"error": "json_parse_failed", "raw": content[:500]}

    return retry_call(
        _call,
        is_error=lambda r: isinstance(r, dict) and "error" in r,
        label="llm",
    )


async def run_analyst_async(system_prompt: str, context: dict, model: str = MODEL) -> dict:
    """Async wrapper — runs run_analyst in a thread so asyncio.gather works."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: run_analyst(system_prompt, context, model))


# ---------------------------------------------------------------------------
# Multi-agent orchestrated decision (grok-4.20-multi-agent-0309)
# ---------------------------------------------------------------------------

async def run_orchestrated_decision(context: dict, settings: dict, on_progress=None) -> dict:
    """
    Manually orchestrated pipeline:
      1. technical_analyst, social_analyst, risk_manager run in parallel
      2. decision agent synthesises all three outputs into a final trade decision

    on_progress: optional async callback(stage: str, data: dict) called at each
                 sub-agent transition so callers (bot loop, UI SSE) can show
                 granular progress.

    context keys (combined payload):
      ticker           - str
      current_price    - float
      indicators       - {timeframe: {...}} — for technical_analyst
      flags            - list[str]          — for technical_analyst / risk_manager
      x_search_query   - str (bot flow: social agent calls search_x internally)
      x_posts          - str (UI pre-fetch: skip search_x)
      obv              - {timeframe: float} — for social_analyst
      atr              - {timeframe: float} — for risk_manager
      portfolio_summary- dict              — for risk_manager + decision
      open_position    - dict | None
      decision_history - list

    Returns a unified dict:
      technical_analysis, social_analysis, risk_analysis  — specialist outputs
      action, size_usd, leverage, stop_loss, confidence,
      specialist_agreement, reasoning, key_contradictions  — decision fields
    """
    from agents.technical import analyze as technical_analyze
    from agents.social    import analyze as social_analyze
    from agents.risk      import analyze as risk_analyze
    from agents.decision  import analyze as decision_analyze

    async def _emit(stage, data=None):
        if on_progress:
            await on_progress(stage, data or {})

    ticker = context["ticker"]

    technical_context = {
        "ticker":        ticker,
        "current_price": context.get("current_price"),
        "indicators":    context.get("indicators", {}),
        "flags":         context.get("flags", []),
    }

    social_context = {
        "ticker": ticker,
        "obv":    context.get("obv", {}),
    }
    if "x_posts" in context:
        social_context["x_posts"] = context["x_posts"]
    else:
        social_context["x_search_query"] = context.get("x_search_query", "")

    risk_context = {
        "ticker":            ticker,
        "current_price":     context.get("current_price"),
        "atr":               context.get("atr", {}),
        "flags":             context.get("flags", []),
        "portfolio_summary": context.get("portfolio_summary", {}),
        "settings":          settings,
    }

    # ── Step 1: run specialists in parallel ────────────────────────────────
    # Skip risk agent when a position is already open — sell/cover/hold
    # decisions don't need position sizing; saves an LLM call.
    await _emit("specialists_start")

    async def _run_specialist(name, coro):
        await _emit(f"{name}_start")
        result = await coro
        await _emit(f"{name}_done", {"result": result})
        return result

    has_open_position = context.get("open_position") is not None

    if has_open_position:
        technical, social = await asyncio.gather(
            _run_specialist("technical", technical_analyze(technical_context)),
            _run_specialist("social",    social_analyze(social_context)),
        )
        risk = {"skipped": True, "reasoning": "Position already open — no sizing needed"}
    else:
        technical, social, risk = await asyncio.gather(
            _run_specialist("technical", technical_analyze(technical_context)),
            _run_specialist("social",    social_analyze(social_context)),
            _run_specialist("risk",      risk_analyze(risk_context)),
        )

    # ── Step 2: decision agent synthesises ────────────────────────────────
    await _emit("decision_start")

    decision_context = {
        "ticker":             ticker,
        "current_price":      context.get("current_price"),
        "technical_analysis": technical,
        "social_analysis":    social,
        "risk_analysis":      risk,
        "open_position":      context.get("open_position"),
        "decision_history":   context.get("decision_history", []),
        "portfolio_summary":  context.get("portfolio_summary", {}),
    }

    decision = await decision_analyze(decision_context)
    await _emit("decision_done", {"result": decision})

    return {
        **decision,
        "technical_analysis": technical,
        "social_analysis":    social,
        "risk_analysis":      risk,
    }

