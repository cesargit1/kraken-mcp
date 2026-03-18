# Kraken MCP — Requirements Document

> **Date:** March 18, 2026  
> **Status:** As-built (derived from code review)

---

## 1. Product Overview

An **autonomous AI-driven paper trading bot** that monitors a configurable watchlist of tokenized equities (xStocks) and crypto pairs on Kraken Exchange. Every polling cycle it fetches OHLCV candles, computes technical indicators, and — when signal thresholds are breached — fires a multi-agent LLM pipeline that produces a trade decision. **All trades are paper-simulated only. No orders are ever submitted to Kraken or any other exchange.** A web dashboard provides real-time visibility into bot state, positions, P&L, and trade history.

---

## 2. Functional Requirements

### 2.1 Market Data

| ID | Requirement |
|----|-------------|
| MD-1 | Fetch OHLCV candles from Kraken CLI for 4 timeframes: `1h`, `4h`, `1d`, `1w` |
| MD-2 | Fall back to Yahoo Finance if Kraken CLI fails for a given ticker |
| MD-3 | Store only new candles incrementally (upsert by `ticker + timeframe + ts`) |
| MD-4 | Skip the most-recent incomplete (open) candle from Kraken responses |
| MD-5 | Candle window: 50 bars for 1h/4h, 100 bars for 1d/1w |
| MD-6 | Resolve current price via: Kraken CLI → Kraken public REST → latest DB candle close |

### 2.2 Technical Indicators

| ID | Requirement |
|----|-------------|
| IND-1 | Compute the following per timeframe: RSI(14), MACD(12/26/9), Bollinger Bands(20), EMA(20), EMA(50), OBV, ATR(14) |
| IND-2 | Compute VWAP for intraday timeframes (1h, 4h) only |
| IND-3 | Detect price peaks and troughs using `scipy.signal.find_peaks` |
| IND-4 | Require at least 20 candles before computing; return `None` if insufficient |
| IND-5 | Upsert indicator snapshot to DB after each computation |
| IND-6 | Raise threshold flags when: RSI < 30 (oversold) or > 70 (overbought); MACD histogram sign change (cross); BB breakout above upper or below lower band; BB squeeze (band width < 4%); EMA golden/death cross; single-candle price move > ±2% |

### 2.3 AI Agent Pipeline

The pipeline is triggered per-ticker and runs sequentially: three specialist agents in parallel, then a final decision agent.

| ID | Requirement |
|----|-------------|
| AG-1 | **Technical Agent** — analyze chart patterns, multi-timeframe indicators, support/resistance levels, and momentum. Output: `{signal, confidence, pattern, key_levels, timeframe_alignment, strongest_timeframe, reasoning, risk_factors}` |
| AG-2 | **Social Agent** — fetch live X (Twitter) posts via xAI search API, assess sentiment, distinguish hype from real interest, detect viral momentum, cross-reference with OBV. Output: `{signal, confidence, sentiment_score (−100..100), hype_vs_real, viral_detected, obv_confirmation, notable_themes, reasoning, risk_factors}` |
| AG-3 | **Risk Agent** — compute position sizing and stop-loss recommendation against configured operator limits (capital, max USD per position, max leverage, concurrent positions, risk % per trade, default stop-loss %). Output: `{max_position_usd, stop_loss_pct, recommended_leverage, exposure_ok, risk_factors, reasoning}` |
| AG-4 | **Decision Agent** — synthesize all three specialist outputs plus portfolio context and recent decision history into a final executable decision. Output: `{action, size_usd, leverage, stop_loss, confidence, specialist_agreement, reasoning, key_contradictions}` |
| AG-5 | All agents use `grok-4-latest` via the xAI API (OpenAI-compatible library) |
| AG-6 | Specialist agents run in parallel; decision agent runs after all three complete |
| AG-7 | Decision agent receives the last 5 decision history rows (with slimmed indicator snapshots) to enable trend awareness across cycles |
| AG-8 | Decision agent receives enriched open position data: unrealized P&L %, unrealized P&L USD, time in trade (hours) |

### 2.4 Trade Execution (Paper)

| ID | Requirement |
|----|-------------|
| TR-1 | Valid actions: `buy` (go long), `sell` (close long), `short` (go short), `cover` (close short), `hold` (no action) |
| TR-2 | Enforce position state hard rules: cannot buy when already long; cannot short when already short; cannot sell/cover when flat |
| TR-3 | `size_usd` must be positive for entry actions |
| TR-4 | Log every executed action to `agent_log` (with full specialist JSON and decision JSON) and `transaction_ledger` |
| TR-5 | Create/close corresponding row in `positions` table on entry/exit |
| TR-6 | Enforce partial unique index: at most one open position per ticker at any time |

### 2.5 Fees & Costs

| ID | Requirement |
|----|-------------|
| FE-1 | Apply 0.05% taker fee on both entry and exit (applied to `gross_amount`) |
| FE-2 | Apply margin cost on leveraged positions: 12% APR prorated on the borrowed portion for the duration the position was held |
| FE-3 | P&L formula: `(close_price − entry_price) × quantity × leverage` (negated for shorts) `− entry_fee − exit_fee − margin_cost` |

### 2.6 Stop-Loss & Risk Controls

| ID | Requirement |
|----|-------------|
| SL-1 | Mechanical stop-loss: if current price breaches `stop_loss`, close position without AI involvement |
| SL-2 | Trailing stop: each cycle, if price makes a new high-water mark, ratchet the stop upward by `ATR × trailing_stop_atr_mult` |
| SL-3 | Bot must skip trading when market is closed for tokenized assets (NYSE hours: Mon–Fri 09:30–16:00 ET); spot crypto trades 24/7 |

### 2.7 Bot Loop & Timing

| ID | Requirement |
|----|-------------|
| BL-1 | Poll interval: configurable, default 300 seconds |
| BL-2 | AI pipeline timer: trigger pipeline at least once every `ai_timer_min` minutes (default 60) regardless of flags |
| BL-3 | Cooldown: suppress repeat AI triggers within `cooldown_min` minutes of the last run (default 30) |
| BL-4 | Process all watchlist tickers concurrently via `asyncio.gather` |
| BL-5 | Cap concurrent Kraken CLI subprocesses at 3 using a semaphore |
| BL-6 | Retry external calls with delays of 0s → 10s → 20s (3 total attempts) |

### 2.8 Dashboard UI

| ID | Requirement |
|----|-------------|
| UI-1 | Serve a single-page app at port 8000 with 4 tabs: Positions, Watchlist, Agent, Settings |
| UI-2 | **Positions tab:** display open positions (entry price, current price, unrealized P&L), closed positions (realized P&L, fees, close reason), portfolio summary (total capital, deployed, realized P&L, win rate) |
| UI-3 | **Watchlist tab:** list all tickers with active/inactive toggle; add/remove tickers; edit `search_name`, `pair`, `asset_class`, `source` |
| UI-4 | **Agent tab:** paginated table of all AI pipeline runs (ticker, action, confidence, timestamp); expandable row showing full specialist analysis JSON; SSE stream to run a live pipeline against any ticker |
| UI-5 | **Settings tab:** form to view and update all `bot_settings` fields |
| UI-6 | **Bot status widget:** show per-ticker live pipeline stage (candles, indicators, stop_loss, specialists, decision, execution) updated from in-memory state without DB polling |
| UI-7 | Bot loop runs as a background asyncio task inside the same uvicorn process |
| UI-8 | SSE endpoint `/stream/agent/{ticker}` emits step-by-step events for the live AI run including intermediate social analysis streaming |

---

## 3. Non-Functional Requirements

| ID | Requirement |
|----|-------------|
| NF-1 | **Security:** Serve `Content-Security-Policy` header on all HTML responses |
| NF-2 | **Resilience:** Supabase client auto-resets on HTTP/2 connection errors and retries the query once |
| NF-3 | **Resilience:** All Kraken CLI and external API calls wrapped in retry logic (3 attempts) |
| NF-4 | **Observability:** Full indicator snapshot stored in `agent_log.indicators_snapshot` for every pipeline run |
| NF-5 | **Deployability:** `Procfile` + `nixpacks.toml` for cloud deployment; `run.py` entrypoint starts bot + UI together in a single process |
| NF-6 | **Paper only:** All trades are DB-tracked simulations. Kraken CLI is used exclusively for price data and candle fetching. No orders are submitted to any exchange. |

---

## 4. Data Model Summary

| Table | Purpose |
|-------|---------|
| `watchlist` | Tickers to monitor (ticker, pair, asset_class, source, active) |
| `candles` | OHLCV history per ticker/timeframe |
| `indicators` | Pre-computed indicator snapshots per ticker/timeframe/ts |
| `signal_state` | Per-ticker: last signal, last AI run timestamp (for timer logic) |
| `cooldown` | Log of cooldown triggers (prevents rapid AI re-fires) |
| `agent_log` | Full record of every AI decision session and specialist outputs |
| `positions` | Open and closed position lifecycle with P&L |
| `transaction_ledger` | Canonical per-leg trade record (entry/exit, fees, P&L) |
| `bot_settings` | Single-row operator configuration (id=1) |

---

## 5. Configuration (bot_settings)

| Key | Default | Description |
|-----|---------|-------------|
| `paper_capital` | $1,000 | Total simulated capital |
| `max_position_pct` | — | Max % of capital per position |
| `max_position_usd` | — | Hard USD cap per position |
| `max_leverage` | — | Maximum leverage allowed |
| `max_open_positions` | — | Maximum concurrent open positions |
| `risk_per_trade_pct` | — | % of capital at risk per trade |
| `stop_loss_pct_default` | — | Default stop-loss if AI doesn't specify |
| `trailing_stop_atr_mult` | — | ATR multiplier for trailing stop ratchet |
| `poll_interval_sec` | 300 | Candle fetch + indicator cycle interval |
| `ai_timer_min` | 60 | Force AI run at least this often |
| `cooldown_min` | 30 | Suppress AI re-trigger within this window |

---

## 6. External Dependencies

| Dependency | Purpose |
|------------|---------|
| xAI API (`grok-4-latest`) | All LLM inference (analysts + X search) |
| Kraken CLI (`~/.cargo/bin/kraken`) | Candle fetch, price quotes, paper orders |
| Kraken public REST API | HTTP fallback for current price |
| Yahoo Finance (`yfinance`) | Candle fallback for tickers unsupported by Kraken CLI |
| Supabase | Postgres-as-a-service for all persistent storage |
| FastAPI + uvicorn | API server and SPA host |
| Jinja2 | Server-side HTML rendering |
| pandas-ta, scipy, numpy, pandas | Technical indicator computation |

---

## 7. Out of Scope

- Live order execution on any exchange (this is a paper trading engine only)
- Backtesting engine
- Multi-exchange support
- User authentication on the dashboard
- Alerts / notifications (email, SMS, webhook)
