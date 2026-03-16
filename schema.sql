-- ============================================================
-- Trading Bot — Supabase Schema
-- Run this in the Supabase SQL editor to set up all tables.
-- ============================================================

-- ------------------------------------------------------------
-- watchlist: active tickers the bot monitors
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watchlist (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL UNIQUE,          -- e.g. 'NVDAx', 'XXBTZUSD'
    source      TEXT NOT NULL DEFAULT 'kraken_xstock',  -- 'kraken_xstock' | 'kraken_crypto' | 'yahoo'
    pair        TEXT,                          -- Kraken pair format: 'NVDAx/USD', 'XXBTZUSD'
    asset_class TEXT DEFAULT 'spot',           -- for --asset-class flag ('spot' | 'tokenized_asset')
    search_name TEXT,                          -- human-readable name for X search (e.g. 'NVDA', 'Bitcoin BTC')
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
-- agent_log: one row per AI decision session (reasoning only — no financials)
-- Financial details live in transaction_ledger, linked via agent_log_id.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_log (
    id                   BIGSERIAL PRIMARY KEY,
    ticker               TEXT NOT NULL,
    pair                 TEXT,                         -- Kraken pair format: 'NVDAx/USD'
    ts                   TIMESTAMPTZ DEFAULT NOW(),
    action               TEXT NOT NULL,   -- 'buy' | 'sell' | 'short' | 'cover' | 'hold'
    trigger_flags        TEXT,            -- comma-separated flags that triggered this run
    technical_analysis   JSONB,
    social_analysis      JSONB,
    risk_analysis        JSONB,
    decision_reasoning   TEXT,
    decision_json        JSONB,            -- full decision agent response {action, size_usd, leverage, stop_loss, confidence, specialist_agreement, reasoning, key_contradictions}
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
    ticker          TEXT NOT NULL,                -- multiple closed rows allowed; partial index enforces one open row per ticker
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
    realized_pnl    FLOAT,                        -- matches transaction_ledger.realized_pnl
    close_reason    TEXT                          -- 'stop_loss' | 'ai_signal' | 'manual'
);

-- Migration: rename positions columns to match transaction_ledger terminology
-- Run once in Supabase SQL editor if upgrading an existing database:
-- ALTER TABLE positions RENAME COLUMN volume TO quantity;
-- ALTER TABLE positions RENAME COLUMN pnl TO realized_pnl;
-- ALTER TABLE positions ADD COLUMN IF NOT EXISTS high_water_price FLOAT;
-- ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS trailing_stop_atr_mult FLOAT DEFAULT 2.0;
-- ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS poll_interval_sec INTEGER DEFAULT 300;
-- ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS ai_timer_min INTEGER DEFAULT 60;
-- ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS cooldown_min INTEGER DEFAULT 30;
-- Enforces at most one open position per ticker while allowing many closed rows.
CREATE UNIQUE INDEX IF NOT EXISTS positions_open ON positions (ticker) WHERE closed_at IS NULL;

-- Migration: if upgrading an existing database that still has the column-level UNIQUE key:
-- ALTER TABLE positions DROP CONSTRAINT IF EXISTS positions_ticker_key;
-- The UNIQUE INDEX above will be created fresh on the next schema run.

-- ------------------------------------------------------------
-- Seed default watchlist (xStocks + crypto on Kraken)
-- ------------------------------------------------------------
INSERT INTO watchlist (ticker, source, pair, asset_class, search_name) VALUES
    ('AAPLx', 'kraken_xstock', 'AAPLx/USD', 'tokenized_asset', 'AAPL'),
    ('NVDAx', 'kraken_xstock', 'NVDAx/USD', 'tokenized_asset', 'NVDA'),
    ('TSLAx', 'kraken_xstock', 'TSLAx/USD', 'tokenized_asset', 'TSLA'),
    ('SPYx',  'kraken_xstock', 'SPYx/USD',  'tokenized_asset', 'SPY'),
    ('QQQx',  'kraken_xstock', 'QQQx/USD',  'tokenized_asset', 'QQQ'),
    ('XXBTZUSD', 'kraken_crypto', 'XXBTZUSD', 'spot', 'Bitcoin BTC'),
    ('XETHZUSD', 'kraken_crypto', 'XETHZUSD', 'spot', 'Ethereum ETH')
ON CONFLICT (ticker) DO NOTHING;

-- Migration: add search_name column to existing watchlist table
-- ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS search_name TEXT;
-- UPDATE watchlist SET search_name = 'AAPL' WHERE ticker = 'AAPLx';
-- UPDATE watchlist SET search_name = 'NVDA' WHERE ticker = 'NVDAx';
-- UPDATE watchlist SET search_name = 'TSLA' WHERE ticker = 'TSLAx';
-- UPDATE watchlist SET search_name = 'SPY'  WHERE ticker = 'SPYx';
-- UPDATE watchlist SET search_name = 'QQQ'  WHERE ticker = 'QQQx';

-- ------------------------------------------------------------
-- transaction_ledger: canonical record of every exchange event
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_ledger (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- identity
  exchange                TEXT NOT NULL DEFAULT 'kraken',
  transaction_type        TEXT NOT NULL,          -- trade, deposit, withdrawal, transfer, reward, earn, pnl, adjustment
  transaction_subtype     TEXT,                   -- buy, sell, wallet_transfer, futures_transfer, allocate, deallocate
  source_type             TEXT NOT NULL,          -- spot, futures, funding, earn, paper

  external_id             TEXT NOT NULL,          -- primary exchange id for this transaction
  external_parent_id      TEXT,                   -- parent/group/order id
  order_id                TEXT,
  trade_id                TEXT,
  refid                   TEXT,
  client_order_id         TEXT,
  userref                 TEXT,

  -- state
  status                  TEXT NOT NULL,          -- pending, open, closed, completed, canceled, failed, expired
  side                    TEXT,                   -- buy, sell
  direction               TEXT,                   -- in, out
  is_simulated            BOOLEAN NOT NULL DEFAULT FALSE,
  is_margin               BOOLEAN NOT NULL DEFAULT FALSE,
  is_reduce_only          BOOLEAN NOT NULL DEFAULT FALSE,
  is_internal_transfer    BOOLEAN NOT NULL DEFAULT FALSE,

  -- timestamps
  event_time              TIMESTAMPTZ NOT NULL,
  created_at_exchange     TIMESTAMPTZ,
  updated_at_exchange     TIMESTAMPTZ,
  settled_at              TIMESTAMPTZ,

  -- asset / market
  asset                   TEXT,                   -- main asset for non-trade rows
  base_asset              TEXT,                   -- trade base asset
  quote_asset             TEXT,                   -- trade quote asset
  pair_symbol             TEXT,                   -- BTCUSD / BTC/USD
  instrument_symbol       TEXT,                   -- futures symbol
  asset_class             TEXT,
  method                  TEXT,                   -- deposit/withdrawal method
  network                 TEXT,
  address                 TEXT,
  address_tag             TEXT,
  key_name                TEXT,
  tx_hash                 TEXT,

  -- transaction economics
  quantity                NUMERIC(36,18),         -- amount of asset or base quantity
  price                   NUMERIC(36,18),         -- unit price
  gross_amount            NUMERIC(36,18),         -- before fees
  gross_currency          TEXT,                   -- usually quote currency or asset currency
  fee_amount              NUMERIC(36,18),
  fee_asset               TEXT,
  net_amount              NUMERIC(36,18),         -- after fees
  net_currency            TEXT,
  cost                    NUMERIC(36,18),         -- quote spent/received for trades
  cost_currency           TEXT,
  balance_after           NUMERIC(36,18),

  -- optional trade/futures specifics
  leverage                TEXT,
  order_type              TEXT,
  time_in_force           TEXT,
  trigger_price           NUMERIC(36,18),
  realized_pnl            NUMERIC(36,18),
  unrealized_pnl          NUMERIC(36,18),
  pnl_currency            TEXT,
  funding_rate            NUMERIC(24,12),
  funding_amount          NUMERIC(36,18),

  -- earn / staking
  strategy_id             TEXT,
  converted_amount        NUMERIC(36,18),
  converted_asset         TEXT,

  -- ui / notes
  title                   TEXT,
  notes                   TEXT,
  tags                    JSONB,
  reconciliation_status   TEXT,

  -- link back to the AI session that triggered this transaction (NULL for manual/external)
  agent_log_id            BIGINT REFERENCES agent_log(id),

  -- audit
  raw_payload             JSONB NOT NULL,
  source_command          TEXT,
  inserted_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_synced_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT uq_transaction_ledger_exchange_external UNIQUE (exchange, external_id)
);
CREATE INDEX IF NOT EXISTS tl_event_time   ON transaction_ledger (event_time DESC);
CREATE INDEX IF NOT EXISTS tl_type         ON transaction_ledger (transaction_type, source_type);
CREATE INDEX IF NOT EXISTS tl_asset        ON transaction_ledger (asset, base_asset);
CREATE INDEX IF NOT EXISTS tl_pair         ON transaction_ledger (pair_symbol);
CREATE INDEX IF NOT EXISTS tl_order        ON transaction_ledger (order_id) WHERE order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tl_refid        ON transaction_ledger (refid)    WHERE refid    IS NOT NULL;
