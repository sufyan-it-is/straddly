-- ============================================================
--  Straddly — Initial Schema Migration
--  Run once against a fresh PostgreSQL database.
--  All volatile tick tables use UNLOGGED for write performance.
-- ============================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";    -- symbol search

-- ============================================================
--  SYSTEM CONFIG  (credentials, toggles, intervals)
-- ============================================================
CREATE TABLE IF NOT EXISTS system_config (
    key         VARCHAR(100) PRIMARY KEY,
    value       TEXT,
    updated_at  TIMESTAMPTZ  DEFAULT now()
);

-- Seed default config rows
INSERT INTO system_config (key, value) VALUES
    ('dhan_client_id',         ''),
    ('dhan_access_token',      ''),
    ('dhan_token_expiry',      NULL),        -- set automatically by TokenRefresher
    ('auth_mode',              'auto_totp'), -- 'auto_totp' | 'manual'  (switchable from Admin)
    ('trading_mode',           'paper'),          -- 'live' | 'paper'
    ('greeks_poll_interval_s', '15'),
    ('paper_default_balance',  '1000000'),
    ('paper_slippage_ticks',   '1'),
    ('paper_brokerage_mode',   'flat'),            -- 'zero' | 'flat' | 'custom'
    ('paper_brokerage_flat',   '20')
ON CONFLICT (key) DO NOTHING;

-- ============================================================
--  INSTRUMENT MASTER  (loaded from CSVs at startup)
-- ============================================================
CREATE TABLE IF NOT EXISTS instrument_master (
    instrument_token  BIGINT        PRIMARY KEY,
    exchange_segment  VARCHAR(20)   NOT NULL,
    symbol            VARCHAR(100)  NOT NULL,
    underlying        VARCHAR(50),               -- NIFTY, BANKNIFTY, RELIANCE etc.
    instrument_type   VARCHAR(20),               -- EQUITY, FUTIDX, OPTIDX, FUTSTK, OPTSTK, FUTCOM, OPTFUT
    expiry_date       DATE,
    strike_price      NUMERIC(12,2),
    option_type       CHAR(2),                   -- CE | PE
    tick_size         NUMERIC(8,4)   NOT NULL DEFAULT 0.05,
    lot_size          INTEGER        NOT NULL DEFAULT 1,
    -- Tier classification
    tier              CHAR(1)        NOT NULL DEFAULT 'B', -- 'A' = on-demand, 'B' = always-on
    -- Which of the 5 WSes this token is assigned to (deterministic for Tier-B)
    ws_slot           SMALLINT       CHECK (ws_slot BETWEEN 0 AND 4),
    created_at        TIMESTAMPTZ    DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_im_segment        ON instrument_master (exchange_segment);
CREATE INDEX IF NOT EXISTS idx_im_underlying     ON instrument_master (underlying, expiry_date);
CREATE INDEX IF NOT EXISTS idx_im_symbol_trgm    ON instrument_master USING GIN (symbol gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_im_tier           ON instrument_master (tier);
CREATE INDEX IF NOT EXISTS idx_im_expiry         ON instrument_master (expiry_date) WHERE expiry_date IS NOT NULL;

-- ============================================================
--  MARKET DATA  (live tick cache — UNLOGGED for speed)
-- ============================================================
CREATE UNLOGGED TABLE IF NOT EXISTS market_data (
    instrument_token  BIGINT        PRIMARY KEY,
    exchange_segment  VARCHAR(20)   NOT NULL,
    symbol            VARCHAR(100),
    ltp               NUMERIC(12,2),
    open              NUMERIC(12,2),
    high              NUMERIC(12,2),
    low               NUMERIC(12,2),
    close             NUMERIC(12,2),   -- previous session close
    -- bid_depth / ask_depth: JSONB array of {price, qty} — qty stripped at API layer
    -- 20 levels for NIFTY/BANKNIFTY/SENSEX index tokens, 5 levels for all others
    bid_depth         JSONB,
    ask_depth         JSONB,
    ltt               TIMESTAMPTZ,     -- last trade time from exchange
    updated_at        TIMESTAMPTZ      DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_md_segment   ON market_data (exchange_segment);
CREATE INDEX IF NOT EXISTS idx_md_updated   ON market_data (updated_at DESC);

-- ============================================================
--  OPTION CHAIN DATA  (REST skeleton + Greeks — UNLOGGED)
-- ============================================================
CREATE UNLOGGED TABLE IF NOT EXISTS option_chain_data (
    instrument_token    BIGINT         PRIMARY KEY,
    underlying          VARCHAR(20)    NOT NULL,
    expiry_date         DATE           NOT NULL,
    strike_price        NUMERIC(12,2)  NOT NULL,
    option_type         CHAR(2)        NOT NULL,   -- CE | PE
    -- Greeks & IV from POST /optionchain (refreshed every 15s)
    iv                  NUMERIC(12,6),
    delta               NUMERIC(12,6),
    theta               NUMERIC(12,6),
    gamma               NUMERIC(12,6),
    vega                NUMERIC(12,6),
    prev_close          NUMERIC(12,2),
    prev_oi             BIGINT,
    greeks_updated_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_oc_underlying_expiry ON option_chain_data (underlying, expiry_date);
CREATE INDEX IF NOT EXISTS idx_oc_strike            ON option_chain_data (underlying, expiry_date, strike_price);

-- ============================================================
--  SUBSCRIPTION STATE  (Tier-A on-demand tracking)
-- ============================================================
CREATE TABLE IF NOT EXISTS subscription_state (
    instrument_token  BIGINT       PRIMARY KEY REFERENCES instrument_master (instrument_token),
    ws_slot           SMALLINT     NOT NULL CHECK (ws_slot BETWEEN 0 AND 4),
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    subscribed_at     TIMESTAMPTZ  DEFAULT now(),
    -- Tier-A eviction criteria
    in_watchlist      BOOLEAN      NOT NULL DEFAULT FALSE,
    has_open_position BOOLEAN      NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_ss_ws_slot ON subscription_state (ws_slot) WHERE is_active = TRUE;

-- ============================================================
--  PAPER TRADING
-- ============================================================
CREATE TABLE IF NOT EXISTS paper_accounts (
    user_id      UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name VARCHAR(100),
    balance      NUMERIC(16,2)  NOT NULL DEFAULT 1000000.00,
    used_margin  NUMERIC(16,2)  NOT NULL DEFAULT 0.00,
    created_at   TIMESTAMPTZ    DEFAULT now()
);

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID          NOT NULL REFERENCES paper_accounts (user_id),
    instrument_token  BIGINT        NOT NULL,
    symbol            VARCHAR(100),
    exchange_segment  VARCHAR(20),
    side              VARCHAR(4)    NOT NULL,   -- BUY | SELL
    order_type        VARCHAR(6)    NOT NULL,   -- MARKET | LIMIT | SL
    quantity          INTEGER       NOT NULL    CHECK (quantity > 0),
    trigger_price     NUMERIC(12,2),            -- for SL
    limit_price       NUMERIC(12,2),            -- for LIMIT
    fill_price        NUMERIC(12,2),
    filled_qty        INTEGER       NOT NULL DEFAULT 0,
    status            VARCHAR(10)   NOT NULL DEFAULT 'PENDING',
    -- PENDING | FILLED | PARTIAL | REJECTED | CANCELLED
    placed_at         TIMESTAMPTZ   DEFAULT now(),
    filled_at         TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_po_user_id  ON paper_orders (user_id, placed_at DESC);
CREATE INDEX IF NOT EXISTS idx_po_status   ON paper_orders (status) WHERE status IN ('PENDING', 'PARTIAL');

CREATE TABLE IF NOT EXISTS paper_positions (
    user_id           UUID          NOT NULL REFERENCES paper_accounts (user_id),
    instrument_token  BIGINT        NOT NULL,
    symbol            VARCHAR(100),
    exchange_segment  VARCHAR(20),
    quantity          INTEGER       NOT NULL,
    avg_price         NUMERIC(12,4) NOT NULL,
    opened_at         TIMESTAMPTZ   DEFAULT now(),
    PRIMARY KEY (user_id, instrument_token)
);

CREATE INDEX IF NOT EXISTS idx_pp_user_id ON paper_positions (user_id) WHERE quantity != 0;

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id          UUID          REFERENCES paper_orders (order_id),
    user_id           UUID          NOT NULL,
    instrument_token  BIGINT        NOT NULL,
    symbol            VARCHAR(100),
    side              VARCHAR(4)    NOT NULL,
    quantity          INTEGER       NOT NULL,
    fill_price        NUMERIC(12,2) NOT NULL,
    realised_pnl      NUMERIC(16,2),
    traded_at         TIMESTAMPTZ   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pt_user_id ON paper_trades (user_id, traded_at DESC);

-- ============================================================
--  EXECUTION LOG  (every order event — ALL modes)
-- ============================================================
CREATE TABLE IF NOT EXISTS execution_log (
    log_id            BIGSERIAL     PRIMARY KEY,
    order_id          UUID          NOT NULL,
    user_id           UUID          NOT NULL,
    instrument_token  BIGINT,
    event_type        VARCHAR(20)   NOT NULL,
    -- ORDER_ACCEPTED | PARTIAL_FILL | FULL_FILL | ORDER_REJECTED | ORDER_CANCELLED
    symbol            VARCHAR(100),
    exchange_segment  VARCHAR(20),
    decision_price    NUMERIC(12,2),   -- price at moment of decision
    fill_price        NUMERIC(12,2),   -- actual fill price (bid/ask based)
    fill_qty          INTEGER,
    slippage          NUMERIC(10,4),
    latency_ms        INTEGER,
    reason            VARCHAR(60),     -- rejection reason code
    note              TEXT,
    logged_at         TIMESTAMPTZ      DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_el_order_id ON execution_log (order_id);
CREATE INDEX IF NOT EXISTS idx_el_user_id  ON execution_log (user_id, logged_at DESC);

-- ============================================================
--  WATCHLISTS
-- ============================================================
CREATE TABLE IF NOT EXISTS watchlists (
    watchlist_id      UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID          NOT NULL REFERENCES paper_accounts (user_id),
    name              VARCHAR(100)  NOT NULL DEFAULT 'My Watchlist',
    created_at        TIMESTAMPTZ   DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id      UUID          NOT NULL REFERENCES watchlists (watchlist_id) ON DELETE CASCADE,
    instrument_token  BIGINT        NOT NULL,
    symbol            VARCHAR(100),
    added_at          TIMESTAMPTZ   DEFAULT now(),
    PRIMARY KEY (watchlist_id, instrument_token)
);
