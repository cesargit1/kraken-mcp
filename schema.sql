-- ============================================================
-- Trading Bot — Supabase Schema
-- Run this in the Supabase SQL editor to set up all tables.
-- ============================================================

-- ------------------------------------------------------------
-- watchlist: active tickers the bot monitors
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watchlist (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL UNIQUE,          -- e.g. 'NVDAx'
    source      TEXT NOT NULL DEFAULT 'kraken_xstock',  -- 'kraken_xstock' | 'kraken_crypto' | 'yahoo'
    pair        TEXT,                          -- Kraken pair format: 'NVDAx/USD'
    asset_class TEXT DEFAULT 'tokenized_asset',-- for --asset-class flag
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
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signal_state (
    ticker          TEXT PRIMARY KEY,
    last_signal     JSONB,
    last_event_type TEXT,
    last_ai_run     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ------------------------------------------------------------
-- cooldown: event-type cooldown log per ticker
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cooldown (
    id           BIGSERIAL PRIMARY KEY,
    ticker       TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    triggered_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cooldown_lookup ON cooldown (ticker, event_type, triggered_at DESC);

-- ------------------------------------------------------------
-- trade_log: full audit trail for every AI decision + execution
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trade_log (
    id                   BIGSERIAL PRIMARY KEY,
    ticker               TEXT NOT NULL,
    ts                   TIMESTAMPTZ DEFAULT NOW(),
    action               TEXT NOT NULL,   -- 'buy' | 'sell' | 'short' | 'hold'
    size                 FLOAT,
    leverage             INTEGER DEFAULT 1,
    stop_loss            FLOAT,
    entry_price          FLOAT,
    technical_analysis   JSONB,
    social_analysis      JSONB,
    risk_analysis        JSONB,
    decision_reasoning   TEXT,
    executed             BOOLEAN DEFAULT false,
    execution_result     JSONB
);
CREATE INDEX IF NOT EXISTS trade_log_lookup ON trade_log (ticker, ts DESC);

-- ------------------------------------------------------------
-- positions: one row per open position (UPSERT on open, update on close)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS positions (
    id              BIGSERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL UNIQUE,         -- one open position per ticker at a time
    side            TEXT NOT NULL,                -- 'long' | 'short'
    volume          FLOAT NOT NULL,
    entry_price     FLOAT NOT NULL,
    stop_loss       FLOAT,                        -- absolute price, NOT pct
    leverage        INTEGER DEFAULT 1,
    trade_log_id    INTEGER REFERENCES trade_log(id),
    opened_at       TIMESTAMPTZ DEFAULT NOW(),
    -- filled in when closed (NULL = still open)
    closed_at       TIMESTAMPTZ,
    close_price     FLOAT,
    pnl             FLOAT,
    close_reason    TEXT                          -- 'stop_loss' | 'ai_signal' | 'manual'
);
CREATE INDEX IF NOT EXISTS positions_open ON positions (ticker) WHERE closed_at IS NULL;

-- ------------------------------------------------------------
-- Seed default watchlist (xStocks on Kraken)
-- ------------------------------------------------------------
INSERT INTO watchlist (ticker, source, pair, asset_class) VALUES
    ('AAPLx', 'kraken_xstock', 'AAPLx/USD', 'tokenized_asset'),
    ('NVDAx', 'kraken_xstock', 'NVDAx/USD', 'tokenized_asset'),
    ('TSLAx', 'kraken_xstock', 'TSLAx/USD', 'tokenized_asset'),
    ('SPYx',  'kraken_xstock', 'SPYx/USD',  'tokenized_asset'),
    ('QQQx',  'kraken_xstock', 'QQQx/USD',  'tokenized_asset')
ON CONFLICT (ticker) DO NOTHING;
