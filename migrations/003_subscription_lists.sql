-- ============================================================
--  Straddly — Migration 003
--  Subscription lists, instrument_master column additions,
--  and scrip master refresh tracking.
-- ============================================================

-- ============================================================
--  INSTRUMENT MASTER — additional columns from CSV
-- ============================================================
ALTER TABLE instrument_master
    ADD COLUMN IF NOT EXISTS isin         VARCHAR(20),
    ADD COLUMN IF NOT EXISTS display_name VARCHAR(200),
    ADD COLUMN IF NOT EXISTS series       VARCHAR(10);

-- ============================================================
--  SUBSCRIPTION LISTS
--  One table, one row per (list_name, symbol) pair.
--  symbol = UNDERLYING_SYMBOL value from the master CSV.
--
--  list_name values:
--    equity          — NSE equity cash
--    options_stocks  — NSE stock options
--    futures_stocks  — NSE stock futures
--    etf             — ETFs
--    mcx_futures     — MCX commodity futures
--    mcx_options     — MCX commodity options
-- ============================================================
CREATE TABLE IF NOT EXISTS subscription_lists (
    id          SERIAL       PRIMARY KEY,
    list_name   VARCHAR(50)  NOT NULL,
    symbol      VARCHAR(200) NOT NULL,   -- UNDERLYING_SYMBOL (always uppercase)
    updated_at  TIMESTAMPTZ  DEFAULT now(),
    UNIQUE (list_name, symbol)
);

CREATE INDEX IF NOT EXISTS idx_sl_list_name ON subscription_lists (list_name);

-- ============================================================
--  SYSTEM CONFIG additions
-- ============================================================
INSERT INTO system_config (key, value) VALUES
    ('scrip_master_refreshed_at',     NULL),
    ('scrip_master_refresh_enabled',  'true')
ON CONFLICT (key) DO NOTHING;
