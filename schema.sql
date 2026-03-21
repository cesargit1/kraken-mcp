-- ============================================================
-- Trading Bot — Supabase Schema (Paper Trading)
-- Run this in the Supabase SQL editor to set up all tables.
-- ============================================================

-- ------------------------------------------------------------
-- watchlist: active tickers the bot monitors
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watchlist (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL UNIQUE,          -- e.g. 'NVDA', 'AAPL', 'BTC-USD'
    asset_class TEXT DEFAULT 'stock',          -- 'stock' | 'crypto'
    search_name TEXT,                          -- human-readable name for X search (e.g. 'NVIDIA NVDA')
    active      BOOLEAN DEFAULT true,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ------------------------------------------------------------
-- candles: OHLCV price history (source of truth)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candles (
    id        BIGSERIAL PRIMARY KEY,
    ticker    TEXT NOT NULL,
    timeframe TEXT NOT NULL,   -- '1h' | '4h' | '1d' | '1w'
    ts        TIMESTAMPTZ NOT NULL,
    open      FLOAT NOT NULL,
    high      FLOAT NOT NULL,
    low       FLOAT NOT NULL,
    close     FLOAT NOT NULL,
    volume    FLOAT NOT NULL,
    UNIQUE (ticker, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS candles_lookup ON candles (ticker, timeframe, ts DESC);

-- ------------------------------------------------------------
-- indicators: pre-computed technical snapshots (never recomputed from API)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS indicators (
    id               BIGSERIAL PRIMARY KEY,
    ticker           TEXT NOT NULL,
    timeframe        TEXT NOT NULL,
    ts               TIMESTAMPTZ NOT NULL,
    rsi              FLOAT,
    macd             FLOAT,
    macd_signal      FLOAT,
    macd_hist        FLOAT,
    bb_upper         FLOAT,
    bb_middle        FLOAT,
    bb_lower         FLOAT,
    ema_20           FLOAT,
    ema_50           FLOAT,
    obv              FLOAT,
    atr              FLOAT,
    vwap             FLOAT,
    peaks_json       JSONB,
    troughs_json     JSONB,
    threshold_flags  JSONB,   -- list of triggered flag names
    computed_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ticker, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS indicators_lookup ON indicators (ticker, timeframe, ts DESC);

-- ------------------------------------------------------------
-- signal_state: last known AI output per ticker (dedup / 60-min timer)
-- Also tracks per-flag cooldown (replaces the separate cooldown table).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signal_state (
    ticker          TEXT PRIMARY KEY,
    last_signal     JSONB,
    last_event_type TEXT,
    last_ai_run     TIMESTAMPTZ,
    cooldown_until  TIMESTAMPTZ,              -- suppress re-trigger until this time
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ------------------------------------------------------------
-- agent_log: one row per AI decision session
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_log (
    id                   BIGSERIAL PRIMARY KEY,
    ticker               TEXT NOT NULL,
    ts                   TIMESTAMPTZ DEFAULT NOW(),
    action               TEXT NOT NULL,   -- 'buy' | 'sell' | 'short' | 'cover' | 'hold'
    trigger_flags        TEXT,            -- comma-separated flags that triggered this run
    technical_analysis   JSONB,
    social_analysis      JSONB,
    risk_analysis        JSONB,
    decision_reasoning   TEXT,
    decision_json        JSONB,            -- full decision agent response
    position_side        TEXT,           -- 'long' | 'short' | 'flat' (at time of run)
    indicators_snapshot  JSONB,           -- {timeframe: {rsi, macd, bb_*, ema_*, obv, atr, vwap, flags}} at time of run
    executed             BOOLEAN DEFAULT false  -- true if at least one transaction was recorded
);
CREATE INDEX IF NOT EXISTS agent_log_lookup ON agent_log (ticker, ts DESC);

-- ------------------------------------------------------------
-- positions: one row per position lifecycle (insert on open, update on close)
-- Multiple closed rows per ticker are allowed; only one open row is enforced
-- via the partial unique index below.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS positions (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,                -- 'long' | 'short'
    quantity        FLOAT NOT NULL,               -- number of units held
    entry_price     FLOAT NOT NULL,
    stop_loss       FLOAT,                        -- absolute price, NOT pct
    high_water_price FLOAT,                       -- best price since entry (for trailing stop)
    leverage        INTEGER DEFAULT 1,
    agent_log_id    INTEGER REFERENCES agent_log(id),
    opened_at       TIMESTAMPTZ DEFAULT NOW(),
    -- filled in when closed (NULL = still open)
    closed_at       TIMESTAMPTZ,
    close_price     FLOAT,
    realized_pnl    FLOAT,
    close_reason    TEXT,                         -- 'stop_loss' | 'ai_signal' | 'manual'
    total_fees      FLOAT,                        -- entry + exit trading fees
    margin_cost     FLOAT                         -- accrued interest on borrowed capital
);
CREATE UNIQUE INDEX IF NOT EXISTS positions_open ON positions (ticker) WHERE closed_at IS NULL;

-- ------------------------------------------------------------
-- Seed default watchlist (Yahoo Finance tickers)
-- ------------------------------------------------------------
INSERT INTO watchlist (ticker, asset_class, search_name) VALUES
    ('AAPL',    'stock',  'Apple AAPL'),
    ('NVDA',    'stock',  'NVIDIA NVDA'),
    ('TSLA',    'stock',  'Tesla TSLA'),
    ('SPY',     'stock',  'S&P 500 SPY'),
    ('QQQ',     'stock',  'Nasdaq QQQ'),
    ('BTC-USD', 'crypto', 'Bitcoin BTC'),
    ('ETH-USD', 'crypto', 'Ethereum ETH')
ON CONFLICT (ticker) DO NOTHING;

-- ------------------------------------------------------------
-- trades: simplified paper trade log (replaces transaction_ledger)
-- One row per executed trade leg (entry or exit).
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id              BIGSERIAL PRIMARY KEY,
    agent_log_id    BIGINT REFERENCES agent_log(id),
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,                -- 'buy' | 'sell'
    action          TEXT NOT NULL,                -- 'buy' | 'sell' | 'short' | 'cover'
    quantity        FLOAT,
    price           FLOAT,                        -- execution price
    cost            FLOAT,                        -- notional USD (quantity * price)
    fee_amount      FLOAT,                        -- trading fee
    leverage        INTEGER DEFAULT 1,
    realized_pnl    FLOAT,                        -- P&L on exit trades
    status          TEXT NOT NULL DEFAULT 'completed',  -- 'completed' | 'failed'
    event_time      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS trades_event_time ON trades (event_time DESC);
CREATE INDEX IF NOT EXISTS trades_ticker     ON trades (ticker, event_time DESC);

-- ------------------------------------------------------------
-- bot_settings: single-row configuration table
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bot_settings (
    id                     INTEGER PRIMARY KEY DEFAULT 1,
    paper_capital          FLOAT NOT NULL DEFAULT 1000,
    max_position_pct       FLOAT NOT NULL DEFAULT 20,
    max_position_usd       FLOAT,
    max_leverage           INTEGER NOT NULL DEFAULT 3,
    max_open_positions     INTEGER NOT NULL DEFAULT 10,
    risk_per_trade_pct     FLOAT NOT NULL DEFAULT 2,
    stop_loss_pct_default  FLOAT NOT NULL DEFAULT 2.5,
    trailing_stop_atr_mult FLOAT NOT NULL DEFAULT 2.0,
    poll_interval_sec      INTEGER NOT NULL DEFAULT 300,
    ai_timer_min           INTEGER NOT NULL DEFAULT 60,
    cooldown_min           INTEGER NOT NULL DEFAULT 30,
    ai_provider            TEXT    NOT NULL DEFAULT 'grok',
    ai_model               TEXT    NOT NULL DEFAULT 'grok-4-1-fast-reasoning',
    updated_at             TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT single_row CHECK (id = 1)
);
-- Add columns for existing deployments (safe no-op if they already exist):
ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS ai_provider TEXT NOT NULL DEFAULT 'grok';
ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS ai_model    TEXT NOT NULL DEFAULT 'grok-4-1-fast-reasoning';
INSERT INTO bot_settings (id) VALUES (1) ON CONFLICT DO NOTHING;
