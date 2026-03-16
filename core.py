"""
core.py — shared runtime for all Kraken trading strategies.
Handles: Grok-4 client, kraken-cli execution, tool dispatch, mode switching (paper/live).
"""

import os
import json
import shutil
import subprocess
import threading
import time
from enum import Enum
from openai import OpenAI
from dotenv import load_dotenv
from retry import retry_call

load_dotenv()

# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------

class Mode(str, Enum):
    PAPER = "paper"
    LIVE  = "live"

MODE = Mode(os.getenv("TRADING_MODE", "paper"))
_default_bin = os.path.expanduser("~/.cargo/bin/kraken")
KRAKEN_BIN = _default_bin if os.path.isfile(_default_bin) else (shutil.which("kraken") or _default_bin)


# ---------------------------------------------------------------------------
# Ticker helpers (used by bot.py and ui_server.py)
# ---------------------------------------------------------------------------

def get_search_name(ticker_row: dict) -> str:
    """Human-readable name for social media search queries.
    Uses the explicit search_name column if set, otherwise derives from ticker."""
    name = ticker_row.get("search_name")
    if name:
        return name
    # Fallback: strip 'x' suffix for xStocks, use ticker as-is otherwise
    ticker = ticker_row["ticker"]
    source = ticker_row.get("source", "")
    if "xstock" in source:
        return ticker.removesuffix("x")
    return ticker


def build_x_query(ticker_row: dict) -> str:
    """Build an X search query appropriate to the asset type."""
    name = get_search_name(ticker_row)
    source = ticker_row.get("source", "")
    if "crypto" in source:
        return (
            f"Search X for posts about ${name} cryptocurrency in the last hour. "
            "Include post texts, volume trends, and any notable news or sentiment."
        )
    return (
        f"Search X for posts about ${name} stock in the last hour. "
        "Include post texts, volume trends, and any notable news or sentiment."
    )


# Global rate limiter — caps concurrent Kraken CLI subprocesses across all
# callers (candle fetches, price checks, balance calls, order execution).
# With 10 tickers × 4 timeframes = 40 potential simultaneous calls; this
# keeps at most 3 in flight at any time and spaces them 200 ms apart.
_KRAKEN_LOCK  = threading.Semaphore(3)
_KRAKEN_DELAY = 0.2  # seconds between calls

# ---------------------------------------------------------------------------
# Grok-4 client (lazy — created on first use so missing key only fails at
# AI call time, not at import/startup)
# ---------------------------------------------------------------------------

_client: OpenAI | None = None
MODEL = "grok-4-latest"


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("XAI_API_KEY")
        if not api_key:
            raise RuntimeError("XAI_API_KEY environment variable is not set")
        _client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    return _client

# ---------------------------------------------------------------------------
# kraken-cli runner
# ---------------------------------------------------------------------------

def _run_kraken_once(args: list[str]) -> dict:
    """Single attempt to execute a kraken-cli command. Returns parsed JSON."""
    with _KRAKEN_LOCK:
        time.sleep(_KRAKEN_DELAY)
        try:
            result = subprocess.run(
                [KRAKEN_BIN] + args + ["-o", "json"],
                capture_output=True, text=True, timeout=30,
            )
            if result.stdout.strip():
                try:
                    return json.loads(result.stdout)
                except json.JSONDecodeError:
                    return {"error": result.stdout}
            return {"error": result.stderr or "empty response"}
        except subprocess.TimeoutExpired:
            return {"error": "kraken-cli timed out"}
        except FileNotFoundError:
            return {"error": f"kraken not found at {KRAKEN_BIN}"}
        except Exception as e:
            return {"error": str(e)}


def run_kraken(args: list[str]) -> dict:
    """Execute a kraken-cli command with retries."""
    if not os.path.isfile(KRAKEN_BIN):
        return {"error": f"kraken not found at {KRAKEN_BIN}"}
    return retry_call(
        _run_kraken_once, args,
        is_error=lambda r: isinstance(r, dict) and "error" in r,
        label="kraken",
    )

# ---------------------------------------------------------------------------
# Unified tool dispatcher — paper or live
# ---------------------------------------------------------------------------

def dispatch_tool(name: str, args: dict, mode: Mode = MODE) -> str:
    """Route tool calls to paper or live kraken-cli commands."""

    asset_flags = []
    if args.get("asset_class") and args["asset_class"] != "spot":
        asset_flags = ["--asset-class", args["asset_class"]]

    limit_flags = []
    if args.get("order_type") == "limit" and "price" in args:
        limit_flags = ["--type", "limit", "--price", str(args["price"])]

    if name == "ticker":
        cmd = ["ticker", args["pair"]] + asset_flags

    elif name == "orderbook":
        cmd = ["orderbook", args["pair"]]
        if "count" in args:
            cmd += ["--count", str(args["count"])]

    elif name == "ohlc":
        cmd = ["ohlc", args["pair"]]
        if "interval" in args:
            cmd += ["--interval", str(args["interval"])]

    elif name == "balance":
        if mode == Mode.PAPER:
            cmd = ["paper", "balance"]
        else:
            cmd = ["balance"]

    elif name == "buy":
        vol = str(args["volume"])
        leverage_flags = ["--leverage", str(args["leverage"])] if args.get("leverage", 1) > 1 else []
        if mode == Mode.PAPER:
            cmd = ["paper", "buy", args["pair"], vol] + limit_flags + leverage_flags
        else:
            cmd = ["order", "buy", args["pair"], vol] + limit_flags + leverage_flags + asset_flags

    elif name == "sell":
        vol = str(args["volume"])
        leverage_flags = ["--leverage", str(args["leverage"])] if args.get("leverage", 1) > 1 else []
        reduce_flags = ["--reduce-only"] if args.get("reduce_only") else []
        if mode == Mode.PAPER:
            cmd = ["paper", "sell", args["pair"], vol] + limit_flags + leverage_flags + reduce_flags
        else:
            cmd = ["order", "sell", args["pair"], vol] + limit_flags + leverage_flags + reduce_flags + asset_flags

    elif name == "cancel_order":
        if mode == Mode.PAPER:
            cmd = ["paper", "cancel", args["order_id"]]
        else:
            cmd = ["order", "cancel", args["order_id"]]

    elif name == "open_orders":
        if mode == Mode.PAPER:
            cmd = ["paper", "orders"]
        else:
            cmd = ["open-orders"]

    elif name == "status":
        if mode == Mode.PAPER:
            cmd = ["paper", "status"]
        else:
            cmd = ["balance"]

    elif name == "trade_history":
        if mode == Mode.PAPER:
            cmd = ["paper", "history"]
        else:
            cmd = ["trades-history"]

    else:
        return json.dumps({"error": f"unknown tool: {name}"})

    result = run_kraken(cmd)
    return json.dumps(result, indent=2)

# ---------------------------------------------------------------------------
# Shared tool schema
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "ticker",
            "description": "Get live price and 24h stats. For xStocks use pair like 'AAPLx/USD' and asset_class='tokenized_asset'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string"},
                    "asset_class": {"type": "string", "enum": ["spot", "tokenized_asset", "forex"]},
                },
                "required": ["pair"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "orderbook",
            "description": "Get live order book (bids/asks) for a pair.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string"},
                    "count": {"type": "integer", "description": "Depth per side (default 10)"},
                },
                "required": ["pair"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ohlc",
            "description": "Get OHLC candlestick data for technical analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string"},
                    "interval": {"type": "integer", "description": "Minutes: 1,5,15,30,60,240,1440"},
                },
                "required": ["pair"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "balance",
            "description": "Get current account balances.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buy",
            "description": "Place a buy order. Market or limit. Use leverage>1 for margin longs (up to 3x on xStocks top 10).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string"},
                    "volume": {"type": "number"},
                    "order_type": {"type": "string", "enum": ["market", "limit"]},
                    "price": {"type": "number", "description": "Limit price (limit orders only)"},
                    "leverage": {"type": "integer", "description": "Margin multiplier: 1 (default, no margin), 2, or 3"},
                    "asset_class": {"type": "string", "enum": ["spot", "tokenized_asset", "forex"]},
                },
                "required": ["pair", "volume"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sell",
            "description": "Place a sell order. Use leverage>1 to open a short position on margin. Use reduce_only=true to close an existing margin position.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string"},
                    "volume": {"type": "number"},
                    "order_type": {"type": "string", "enum": ["market", "limit"]},
                    "price": {"type": "number", "description": "Limit price (limit orders only)"},
                    "leverage": {"type": "integer", "description": "Margin multiplier: 1 (default), 2, or 3. Set >1 to short."},
                    "reduce_only": {"type": "boolean", "description": "True to close an existing margin position only"},
                    "asset_class": {"type": "string", "enum": ["spot", "tokenized_asset", "forex"]},
                },
                "required": ["pair", "volume"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_orders",
            "description": "List all open orders.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Cancel an open order by ID.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "status",
            "description": "Get portfolio summary: total value, P&L, positions.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trade_history",
            "description": "Get filled trade history.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

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
    Retries on API errors and JSON parse failures.
    """
    def _call():
        response = _get_client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": json.dumps(context, indent=2)},
            ],
        )
        content = response.choices[0].message.content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            import re
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


def run_agent(system_prompt: str, user_prompt: str, mode: Mode = MODE, verbose: bool = True, model: str = MODEL) -> str:
    """
    Run a single agent turn with full tool-call loop.
    Returns the final text response.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    while True:
        response = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            return msg.content

        messages.append(msg)
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            if verbose:
                arg_str = ", ".join(f"{k}={v}" for k, v in args.items())
                tag = f"[{mode.upper()}]" if mode == Mode.LIVE else "[paper]"
                print(f"  \033[90m{tag} {tc.function.name}({arg_str})\033[0m", flush=True)
            result = dispatch_tool(tc.function.name, args, mode)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
