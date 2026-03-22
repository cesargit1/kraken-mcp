# Next.js Rebuild Requirements

## Overview

Rebuild the **xStocks Quant Bot Dashboard** as a modern Next.js 15 (App Router) + React 19 application. The current stack is a FastAPI + Jinja2 SPA with vanilla JS; the goal is a fully typed, component-driven frontend while keeping the existing Python backend API intact.

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Framework | Next.js 15 (App Router) | `create-next-app`, TypeScript strict |
| UI library | React 19 | Server + Client Components |
| Styling | Tailwind CSS v4 | Keep dark theme (`#131722` base), same color tokens |
| Charts | Lightweight Charts v4 (`lightweight-charts`) | TradingView OSS, candlestick only |
| Data fetching | TanStack Query v5 | Auto-polling, cache invalidation |
| State | Zustand | Lightweight global: bot status, active tab |
| SSE streaming | Native `EventSource` in a custom React hook | One hook per SSE endpoint |
| HTTP client | `fetch` (native) | No Axios needed |
| Forms | React Hook Form + Zod | Settings and watchlist forms |
| Linting | ESLint + Prettier | Standard Next.js config |
| Package manager | pnpm | |

---

## Architecture

```
next-app/
├── app/
│   ├── layout.tsx              # Root layout: sidebar + font + providers
│   ├── page.tsx                # Redirect → /portfolio
│   ├── portfolio/
│   │   └── page.tsx
│   ├── candles/
│   │   └── page.tsx
│   ├── agents/
│   │   └── page.tsx
│   ├── watchlist/
│   │   └── page.tsx
│   └── settings/
│       └── page.tsx
├── components/
│   ├── layout/
│   │   ├── Sidebar.tsx
│   │   └── MobileNav.tsx
│   ├── portfolio/
│   │   ├── StatsBar.tsx
│   │   ├── OpenPositionsTable.tsx
│   │   ├── ClosedPositionsTable.tsx
│   │   └── TradesTable.tsx
│   ├── candles/
│   │   ├── CandleChart.tsx     # lightweight-charts wrapper (Client Component)
│   │   ├── OhlcvBar.tsx
│   │   └── TickerSelect.tsx
│   ├── agents/
│   │   ├── ActiveRuns.tsx      # Live polling via /api/bot-status
│   │   ├── AgentHistoryTable.tsx
│   │   ├── AgentLogDetail.tsx  # Expandable row detail
│   │   ├── IndicatorsSnapshot.tsx
│   │   ├── ForceRunPanel.tsx   # SSE stream trigger
│   │   └── Pagination.tsx
│   ├── watchlist/
│   │   ├── WatchlistTable.tsx
│   │   └── AddTickerForm.tsx
│   ├── settings/
│   │   └── SettingsForm.tsx
│   └── ui/
│       ├── ActionPill.tsx      # buy/sell/short/cover/hold
│       ├── SidePill.tsx        # LONG/SHORT/FLAT
│       ├── StatCard.tsx        # Reusable 9-tile stat card
│       ├── Toast.tsx           # SSE progress / error toasts
│       └── Spinner.tsx
├── hooks/
│   ├── useSSE.ts               # Generic EventSource hook
│   ├── useAgentStream.ts       # Wraps useSSE for /stream/agent/{ticker}
│   ├── useBotStatus.ts         # Polling hook (3s) for /api/bot-status
│   └── usePositionPrices.ts    # Polling hook for /api/positions/prices
├── lib/
│   ├── api.ts                  # Typed fetch wrappers for all endpoints
│   ├── types.ts                # All shared TypeScript types
│   ├── format.ts               # fmt(), fmtDate(), pillClass()
│   └── constants.ts            # Stage labels, fee rates, etc.
├── providers/
│   └── QueryProvider.tsx       # TanStack Query client setup
└── public/
    └── favicon.ico
```

---

## Routes & Pages

### `/portfolio` — Portfolio

**Data sources:**
- `GET /api/positions` — full snapshot (polled every 30s via TanStack Query)
- `GET /api/positions/prices` — price-only refresh (manual button + auto every 60s)

**Sections:**

**Stats Bar (9 tiles)** — `<StatsBar />`
| Stat | Key | Format |
|---|---|---|
| Starting Capital | `paper_capital` | `$1,234` |
| Cash Invested | `total_cost` | `$1,234` |
| Available Cash | `cash` | `$1,234` |
| Total Exposure | `total_size` | `$1,234` |
| Open Positions | `pos_count` | integer |
| Unrealised P&L | `total_pnl` | `$1,234 (12.3%)` green/red |
| Realised P&L | `realized_pnl` | `$1,234` green/red |
| Total Fees | `total_fees` | `$12.34` |
| Margin Costs | `total_margin_cost` | `$12.34` |

**Open Positions Table** — `<OpenPositionsTable />`

Columns: ticker · side pill · qty · entry price · notional · stop loss (price + %) · current price (live, patched by prices refresh) · unrealised P&L ($ + %) · leverage · margin cost · opened at

Price cells are updated in-place (no full table re-render) when the price refresh fires. Use TanStack Query's `select` to derive price-patched data so React handles the DOM diff.

**Closed Positions Table** — `<ClosedPositionsTable />` (last 50 rows)

Columns: ticker · side · qty · entry · close price · notional · realised P&L · fees · margin cost · close reason · opened at · closed at

**Recent Trades Table** — `<TradesTable />` (last 30 rows)

Columns: `#id` · ticker · action pill · qty · price · notional · leverage · fee · status (✓/✗) · event time

---

### `/candles` — Candles

**Data source:** `GET /api/candles?ticker=&timeframe=&limit=200`

**Components:**
- `<TickerSelect />` — dropdown populated from `GET /api/watchlist`, default to first active ticker
- Timeframe toggle buttons: `1h | 4h | 1d | 1w` (default `1d`)
- `<OhlcvBar />` — 6 stat tiles: Open, High, Low, Close, Volume, Last Updated
- `<CandleChart />` — lightweight-charts candlestick chart, `Client Component`, `use client`, dark theme `#131722`, height 500px. Handle resize with `ResizeObserver`.

When ticker or timeframe changes, refetch from `/api/candles` and replace chart series data.

---

### `/agents` — Agents

**Data sources:**
- `GET /api/bot-status` — polled every 3s, drives Active Runs panel
- `GET /api/agent-history?page={n}` — paginated 25/page
- `GET /api/agent-log/{id}` — lazy-loaded on row expand
- `GET /stream/agent/{ticker}?force=true` — SSE for Force Run

**Sub-sections:**

**Active Runs** — `<ActiveRuns />`

Shows one card per ticker currently in pipeline. Each card shows: ticker, spinner, stage label (human-readable, see stage map below), source badge (`bot` = cyan · `manual` = indigo).

Stage label map:
```ts
const STAGE_LABELS: Record<string, string> = {
  candles:        'Fetching Candles',
  indicators:     'Computing Indicators',
  price_check:    'Checking Price',
  stop_loss:      'Stop-Loss Check',
  flag_check:     'Checking Flags',
  ai_pipeline:    'AI Pipeline',
  context_fetch:  'Fetching Context',
  specialists:    'Running Specialists',
  x_search:       'X Search',
  technical:      'Technical Analysis',
  social:         'Social Analysis',
  risk:           'Risk Assessment',
  decision:       'Generating Decision',
  trade:          'Executing Trade',
  execution:      'Trade Execution',
  complete:       'Complete',
  skipped:        'Skipped',
  no_data:        'No Data',
}
```

**Agent Run History** — `<AgentHistoryTable />`

Columns: id · timestamp · ticker · position side pill · action pill · trigger flags

Row click expands inline `<AgentLogDetail />` (lazy load). State: which row id is open (null = none). Consider `useReducer` for clarity.

**`<AgentLogDetail />`** — collapsible sections:

1. **Indicators Snapshot** — table, all 4 timeframes. RSI cell: red bg if >70, green bg if <30. MACD histogram: green text if positive, red if negative.
2. **Decision** — structured display: action/confidence/size_usd/leverage/stop_loss/specialist_agreement, reasoning text, key_contradictions list.
3. **Technical Analysis** — collapsible JSON viewer (pretty-printed `<pre>`).
4. **Social Analysis** — collapsible JSON viewer.
5. **Risk Analysis** — collapsible JSON viewer.
6. **Linked Trades** — mini table if any.

**Pagination** — `<Pagination />` Previous/Next + page numbers from `total_pages`.

**Force Run Panel** — `<ForceRunPanel />`

- Ticker text input + "Force Run" button
- On submit: opens `EventSource` at `/stream/agent/{ticker}?force=true`
- Progress shown as streaming step list (each SSE event appends a line)
- On `complete` or `error`: close EventSource, show final toast
- Hook: `useAgentStream(ticker)` returns `{ steps, status, start, stop }`

---

### `/watchlist` — Watchlist

**Data source:** `GET /api/watchlist` (all rows, active + inactive)

**`<AddTickerForm />`**

Fields:
- Ticker symbol (text input, uppercase-forced, required)
- Asset class (select: `stock` | `crypto`, required)
- Search name (text input, used for X queries, optional)

Submit: `POST /api/watchlist` → `{ ticker, asset_class, search_name }`  
On success: invalidate watchlist query, reset form.

**`<WatchlistTable />`**

Columns: ticker · asset class · search name · active (toggle switch)

Per-row actions:
- Toggle active: `PATCH /api/watchlist/{ticker}` `{ active: bool }`
- Edit search_name / asset_class inline: `PATCH /api/watchlist/{ticker}` `{ search_name, asset_class }`
- Delete: `DELETE /api/watchlist/{ticker}` with confirmation dialog

---

### `/settings` — Settings

**Data source:** `GET /api/settings`  
**Save:** `PUT /api/settings`

**`<SettingsForm />`** — React Hook Form + Zod validation

| Field | Type | Default | Validation |
|---|---|---|---|
| Starting Capital ($) | number | 1000 | min 100 |
| Max Position Size (%) | number | 20 | 1–100 |
| Max Position Size USD | number | null | optional, >0 |
| Max Leverage | number | 3 | 1–5 |
| Max Open Positions | number | 10 | 1–50 |
| Max Risk Per Trade (%) | number | 2 | 0.1–25 |
| Default Stop-Loss (%) | number | 2.5 | 0.1–25 |
| Poll Interval (seconds) | number | 300 | 30–3600 |
| AI Timer (minutes) | number | 60 | 5–1440 |
| Cooldown (minutes) | number | 30 | 1–240 |

On save: `PUT /api/settings`, show success/error toast.

---

## TypeScript Types (`lib/types.ts`)

```ts
// Watchlist
export interface WatchlistRow {
  id: number
  ticker: string
  asset_class: 'stock' | 'crypto'
  search_name: string | null
  active: boolean
  created_at: string
}

// Candles
export interface Candle {
  ts: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

// Positions
export interface OpenPosition {
  id: number
  ticker: string
  side: 'long' | 'short'
  quantity: number
  entry_price: number
  stop_loss: number
  leverage: number
  notional: number
  margin_cost: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
  current_price: number
  opened_at: string
}

export interface ClosedPosition {
  id: number
  ticker: string
  side: 'long' | 'short'
  quantity: number
  entry_price: number
  close_price: number
  notional: number
  realized_pnl: number
  total_fees: number
  margin_cost: number
  close_reason: 'stop_loss' | 'ai_signal' | 'manual'
  opened_at: string
  closed_at: string
}

export interface Trade {
  id: number
  ticker: string
  side: 'buy' | 'sell'
  action: 'buy' | 'sell' | 'short' | 'cover'
  quantity: number
  price: number
  cost: number
  fee_amount: number
  leverage: number
  realized_pnl: number
  status: 'completed' | 'failed'
  event_time: string
  agent_log_id: number | null
}

export interface PortfolioSummary {
  paper_capital: number
  total_cost: number
  cash: number
  total_size: number
  pos_count: number
  total_pnl: number
  total_pnl_pct: number
  realized_pnl: number
  total_fees: number
  total_margin_cost: number
}

export interface PositionsResponse {
  summary: PortfolioSummary
  open_positions: OpenPosition[]
  closed_positions: ClosedPosition[]
  recent_trades: Trade[]
  settings: BotSettings
}

// Agent
export type AgentAction = 'buy' | 'sell' | 'short' | 'cover' | 'hold'
export type PositionSide = 'long' | 'short' | 'flat'

export interface AgentLogSummary {
  id: number
  ts: string
  ticker: string
  action: AgentAction
  position_side: PositionSide
  trigger_flags: string | null
  executed: boolean
}

export interface AgentLogDetail extends AgentLogSummary {
  technical_analysis: Record<string, unknown>
  social_analysis: Record<string, unknown>
  risk_analysis: Record<string, unknown>
  decision_json: {
    action: AgentAction
    confidence: number
    size_usd: number | null
    leverage: number
    stop_loss: number | null
    specialist_agreement: 'full' | 'partial' | 'conflicting'
    reasoning: string
    key_contradictions: string[]
  }
  decision_reasoning: string
  indicators_snapshot: Record<string, unknown>
  transactions: Trade[]
}

export interface AgentHistoryResponse {
  logs: AgentLogSummary[]
  page: number
  total_pages: number
}

// Bot status
export type BotStage =
  | 'candles' | 'indicators' | 'price_check' | 'stop_loss'
  | 'flag_check' | 'ai_pipeline' | 'context_fetch' | 'specialists'
  | 'x_search' | 'technical' | 'social' | 'risk' | 'decision'
  | 'trade' | 'execution' | 'complete' | 'skipped' | 'no_data'

export interface TickerBotState {
  ticker: string
  status: 'running' | 'idle' | 'error'
  stage: BotStage
  started_at: string | null
  source: 'bot' | 'manual'
}

export interface BotStatusResponse {
  tickers: TickerBotState[]
  last_cycle_at: string | null
  next_cycle_at: string | null
  poll_interval_sec: number
}

// Settings
export interface BotSettings {
  paper_capital: number
  max_position_pct: number
  max_position_usd: number | null
  max_leverage: number
  max_open_positions: number
  risk_per_trade_pct: number
  stop_loss_pct_default: number
  trailing_stop_atr_mult: number
  poll_interval_sec: number
  ai_timer_min: number
  cooldown_min: number
  ai_provider: string
  ai_model: string
  updated_at: string
}

// SSE events
export type SSEEventType =
  | 'candles_start' | 'candles_done'
  | 'indicators_start' | 'indicators_done'
  | 'no_trigger' | 'price'
  | 'specialists_start'
  | 'technical_done' | 'social_agent_done' | 'risk_done' | 'decision_done'
  | 'guard' | 'trade_start' | 'trade_done' | 'trade_skipped'
  | 'complete' | 'error'

export interface SSEStep {
  type: SSEEventType
  data: Record<string, unknown>
  timestamp: string
}
```

---

## API Client (`lib/api.ts`)

Wrap all backend calls as typed async functions. The Python FastAPI backend continues to run alongside Next.js; configure `next.config.ts` to proxy `/api/*` and `/stream/*` to `http://localhost:8000` (or the deployed backend URL via `NEXT_PUBLIC_API_BASE`).

```ts
// next.config.ts rewrites example
const API_BASE = process.env.API_BASE ?? 'http://localhost:8000'

rewrites: async () => [
  { source: '/api/:path*', destination: `${API_BASE}/api/:path*` },
  { source: '/stream/:path*', destination: `${API_BASE}/stream/:path*` },
]
```

Functions to implement:
- `fetchWatchlist(): Promise<WatchlistRow[]>`
- `addWatchlistTicker(data): Promise<WatchlistRow>`
- `patchWatchlistTicker(ticker, data): Promise<WatchlistRow>`
- `deleteWatchlistTicker(ticker): Promise<void>`
- `fetchPositions(): Promise<PositionsResponse>`
- `fetchPositionPrices(): Promise<{ prices: Record<string, number>; pnl: Record<string, number> }>`
- `fetchCandles(ticker, timeframe, limit?): Promise<Candle[]>`
- `fetchSettings(): Promise<BotSettings>`
- `saveSettings(data): Promise<BotSettings>`
- `fetchBotStatus(): Promise<BotStatusResponse>`
- `fetchAgentHistory(page): Promise<AgentHistoryResponse>`
- `fetchAgentLog(id): Promise<AgentLogDetail>`

---

## Hooks

### `useSSE(url: string | null)`

```ts
// Returns { steps: SSEStep[], status: 'idle' | 'connecting' | 'streaming' | 'done' | 'error' }
// Opens EventSource when url is non-null, closes on null / unmount
// Filters keep-alive comments (lines starting with ':')
```

### `useAgentStream(ticker: string | null)`

Wraps `useSSE` with `/stream/agent/${ticker}?force=true`. Returns `{ steps, status, run(ticker), stop }`.

### `useBotStatus()`

TanStack Query with `refetchInterval: 3000`. Returns bot status data.

### `usePositionPrices(enabled: boolean)`

TanStack Query with `refetchInterval: 60_000`. Merges price data into open positions.

---

## Styling

- **Base color:** `#131722` (chart + page background)
- **Surface:** `bg-zinc-900` / `bg-zinc-800`
- **Border:** `border-zinc-700`
- **Text primary:** `text-zinc-100`
- **Text muted:** `text-zinc-400`
- **Accent green:** `text-emerald-400` / `bg-emerald-900/30`
- **Accent red:** `text-red-400` / `bg-red-900/30`
- **Accent yellow:** `text-yellow-400` / `bg-yellow-900/30`
- **Accent cyan:** `text-cyan-400` (bot source badge)
- **Accent indigo:** `text-indigo-400` (manual source badge)

**Action pill classes** (`<ActionPill action={...} />`):

| Action | Classes |
|---|---|
| buy / long | `bg-emerald-900/50 text-emerald-300 border border-emerald-700` |
| sell / cover | `bg-yellow-900/50 text-yellow-300 border border-yellow-700` |
| short | `bg-red-900/50 text-red-300 border border-red-700` |
| hold | `bg-zinc-800 text-zinc-400 border border-zinc-600` |

**Sidebar:** Fixed left, `w-56`, `bg-zinc-900 border-r border-zinc-800`. Active link: `bg-zinc-800 text-zinc-100`, inactive: `text-zinc-400 hover:text-zinc-100`.  
Mobile: hidden sidebar + hamburger toggling a full-height overlay.

---

## Rendering Strategy

| Page | Strategy | Reason |
|---|---|---|
| `/portfolio` | Client Component (`'use client'`) | Live polling, dynamic data |
| `/candles` | Client Component | Chart needs browser APIs |
| `/agents` | Client Component | SSE, polling, expandable rows |
| `/watchlist` | Client Component | Mutations + optimistic UI |
| `/settings` | Client Component | Form state |
| Sidebar | Server Component shell, Client interactive parts | Nav links are static |

No Server-Side Rendering needed — the Python backend handles data; Next.js is a pure frontend shell. Use `'use client'` at page level where required.

---

## Environment Variables

```env
# .env.local
API_BASE=http://localhost:8000         # Backend URL for Next.js rewrites (server-side)
NEXT_PUBLIC_API_BASE=                  # Leave empty (rewrites handle it) or set for direct calls
```

---

## Non-Goals (Out of Scope)

- Replacing the Python backend — the FastAPI server continues to run unchanged
- Authentication / login (not in original)
- Dark/light mode toggle (dark only)
- Mobile-first layout (responsive is nice-to-have, not required)
- i18n

---

## Implementation Order

1. [ ] Scaffold `next-app/` with `create-next-app` (TypeScript, Tailwind, App Router, pnpm)
2. [ ] Configure `next.config.ts` rewrites for `/api/*` and `/stream/*`
3. [ ] Define all types in `lib/types.ts`
4. [ ] Implement `lib/api.ts` fetch wrappers
5. [ ] Build layout: `<Sidebar />`, `providers/QueryProvider.tsx`, root `layout.tsx`
6. [ ] Portfolio page: StatsBar → OpenPositionsTable → ClosedPositionsTable → TradesTable
7. [ ] Candles page: TickerSelect → timeframe buttons → OhlcvBar → CandleChart
8. [ ] Agents page: ActiveRuns → AgentHistoryTable → AgentLogDetail → ForceRunPanel
9. [ ] Watchlist page: AddTickerForm → WatchlistTable
10. [ ] Settings page: SettingsForm with Zod validation
11. [ ] Shared UI: ActionPill, SidePill, StatCard, Toast, Spinner, Pagination
12. [ ] Hooks: `useSSE`, `useAgentStream`, `useBotStatus`, `usePositionPrices`
13. [ ] Polish: responsive sidebar, keyboard nav, loading skeletons, error boundaries
