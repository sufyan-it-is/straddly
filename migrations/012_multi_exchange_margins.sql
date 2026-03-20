-- Migration: 012_multi_exchange_margins.sql
-- Purpose: Add multi-exchange (NSE, BSE, MCX) margin and holiday management
-- Created: 2026-02-21
--
-- Changes:
-- 1. Create exchange_holidays table to cache NSE, BSE, MCX trading holidays
-- 2. Extend SPAN/ELM tables to include exchange identifier
-- 3. Add tables for MCX and BSE-specific margin data
-- 4. Add background job tracking for holiday/margin synchronization

-- ────────────────────────────────────────────────────────────────────────────
-- 1.1: Exchange Holidays Table
-- ────────────────────────────────────────────────────────────────────────────
-- Stores trading holidays (non-working days) for NSE, BSE, MCX
-- Updated on 1st trading day of each month at 08:00 IST

CREATE TABLE IF NOT EXISTS exchange_holidays (
    id BIGSERIAL PRIMARY KEY,
    exchange VARCHAR(10) NOT NULL,  -- 'NSE', 'BSE', 'MCX'
    holiday_date DATE NOT NULL,
    holiday_name VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT uk_exchange_holiday UNIQUE(exchange, holiday_date)
);

CREATE INDEX IF NOT EXISTS idx_exchange_holidays_exchange ON exchange_holidays(exchange);
CREATE INDEX IF NOT EXISTS idx_exchange_holidays_date ON exchange_holidays(holiday_date);

COMMENT ON TABLE exchange_holidays IS 'Trading holidays for NSE, BSE, MCX. Updated monthly on 1st trading day at 08:00 IST.';
COMMENT ON COLUMN exchange_holidays.exchange IS 'Exchange code: NSE, BSE, MCX';
COMMENT ON COLUMN exchange_holidays.holiday_date IS 'Non-trading date';
COMMENT ON COLUMN exchange_holidays.holiday_name IS 'Holiday name (e.g., Republic Day, Holi)';


-- ────────────────────────────────────────────────────────────────────────────
-- 1.2: Background Job Log (for monitoring holiday/margin sync)
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS background_jobs (
    id BIGSERIAL PRIMARY KEY,
    job_type VARCHAR(50) NOT NULL,      -- 'holiday_sync', 'margin_download_nse', 'margin_download_mcx', 'margin_download_bse'
    exchange VARCHAR(10),                -- e.g., 'NSE', 'MCX', 'BSE' (NULL for holiday_sync which applies to all)
    scheduled_at TIMESTAMP NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    status VARCHAR(20) NOT NULL,         -- 'pending', 'running', 'success', 'failed'
    message TEXT,
    error_message TEXT,
    details JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT ck_job_status CHECK (status IN ('pending', 'running', 'success', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_bg_jobs_type ON background_jobs(job_type);
CREATE INDEX IF NOT EXISTS idx_bg_jobs_exchange ON background_jobs(exchange);
CREATE INDEX IF NOT EXISTS idx_bg_jobs_status ON background_jobs(status);
CREATE INDEX IF NOT EXISTS idx_bg_jobs_scheduled ON background_jobs(scheduled_at);

COMMENT ON TABLE background_jobs IS 'Background job execution log for holiday syncs and margin downloads';
COMMENT ON COLUMN background_jobs.job_type IS 'Job types: holiday_sync, margin_download_nse, margin_download_mcx, margin_download_bse';


-- ────────────────────────────────────────────────────────────────────────────
-- 1.3: Extend existing span_margin_cache table with exchange
-- ────────────────────────────────────────────────────────────────────────────

-- Check if exchange column already exists; if not, add it
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'span_margin_cache' AND column_name = 'exchange'
    ) THEN
        ALTER TABLE span_margin_cache ADD COLUMN exchange VARCHAR(10) DEFAULT 'NSE';
        ALTER TABLE span_margin_cache ALTER COLUMN exchange DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_span_exchange ON span_margin_cache(exchange);
        CREATE INDEX IF NOT EXISTS idx_span_exchange_symbol ON span_margin_cache(exchange, symbol);
    END IF;
END $$;

COMMENT ON COLUMN span_margin_cache.exchange IS 'Exchange code: NSE, BSE, MCX';


-- ────────────────────────────────────────────────────────────────────────────
-- 1.4: Extend elm_margin_cache table with exchange
-- ────────────────────────────────────────────────────────────────────────────

DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'elm_margin_cache' AND column_name = 'exchange'
    ) THEN
        ALTER TABLE elm_margin_cache ADD COLUMN exchange VARCHAR(10) DEFAULT 'NSE';
        ALTER TABLE elm_margin_cache ALTER COLUMN exchange DROP DEFAULT;
        CREATE INDEX IF NOT EXISTS idx_elm_exchange ON elm_margin_cache(exchange);
        CREATE INDEX IF NOT EXISTS idx_elm_exchange_symbol ON elm_margin_cache(exchange, symbol);
    END IF;
END $$;

COMMENT ON COLUMN elm_margin_cache.exchange IS 'Exchange code: NSE, BSE, MCX';


-- ────────────────────────────────────────────────────────────────────────────
-- 1.5: MCX-specific tables
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mcx_span_margin_cache (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(50) NOT NULL,
    ref_price NUMERIC(18, 4) NOT NULL,
    price_scan NUMERIC(18, 4) NOT NULL,      -- SPAN scan range per underlying unit
    contract_value_factor NUMERIC(10, 4) NOT NULL DEFAULT 1.0,
    elm_pct NUMERIC(7, 2) NOT NULL DEFAULT 3.0,
    downloaded_at DATE NOT NULL,
    is_latest BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mcx_span_symbol ON mcx_span_margin_cache(symbol);
CREATE INDEX IF NOT EXISTS idx_mcx_span_date ON mcx_span_margin_cache(downloaded_at);
CREATE INDEX IF NOT EXISTS idx_mcx_span_latest ON mcx_span_margin_cache(is_latest);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcx_span_latest_unique ON mcx_span_margin_cache(symbol) WHERE is_latest;

COMMENT ON TABLE mcx_span_margin_cache IS 'MCX commodities SPAN margin data. Updated daily at 08:45 IST.';
COMMENT ON COLUMN mcx_span_margin_cache.contract_value_factor IS 'Contract multiplier (Gold: 100g, Silver: 30kg, etc.)';


-- ────────────────────────────────────────────────────────────────────────────
-- 1.6: BSE-specific tables
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bse_span_margin_cache (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(50) NOT NULL,
    ref_price NUMERIC(18, 4) NOT NULL,
    price_scan NUMERIC(18, 4) NOT NULL,      -- SPAN scan range per underlying unit
    contract_value_factor NUMERIC(10, 4) NOT NULL DEFAULT 1.0,
    elm_pct NUMERIC(7, 2) NOT NULL DEFAULT 3.0,
    downloaded_at DATE NOT NULL,
    is_latest BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bse_span_symbol ON bse_span_margin_cache(symbol);
CREATE INDEX IF NOT EXISTS idx_bse_span_date ON bse_span_margin_cache(downloaded_at);
CREATE INDEX IF NOT EXISTS idx_bse_span_latest ON bse_span_margin_cache(is_latest);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bse_span_latest_unique ON bse_span_margin_cache(symbol) WHERE is_latest;

COMMENT ON TABLE bse_span_margin_cache IS 'BSE derivatives SPAN margin data. Updated daily at 08:45 IST.';


-- ────────────────────────────────────────────────────────────────────────────
-- 1.7: Margin download logs (unified across exchanges)
-- ────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS margin_download_logs (
    id BIGSERIAL PRIMARY KEY,
    exchange VARCHAR(10) NOT NULL,        -- 'NSE', 'BSE', 'MCX'
    download_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL,          -- 'success', 'partial', 'failed'
    symbol_count INTEGER DEFAULT 0,
    elm_entries INTEGER DEFAULT 0,
    error_message TEXT,
    file_sources JSONB DEFAULT '{}',     -- URLs/sources downloaded from
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT ck_margin_dl_status CHECK (status IN ('success', 'partial', 'failed')),
    CONSTRAINT uk_margin_dl UNIQUE(exchange, download_date)
);

CREATE INDEX IF NOT EXISTS idx_margin_dl_exchange ON margin_download_logs(exchange);
CREATE INDEX IF NOT EXISTS idx_margin_dl_date ON margin_download_logs(download_date);
CREATE INDEX IF NOT EXISTS idx_margin_dl_status ON margin_download_logs(status);

COMMENT ON TABLE margin_download_logs IS 'Audit trail for margin file downloads across all exchanges';


-- ────────────────────────────────────────────────────────────────────────────
-- 2.0: Helper Functions
-- ────────────────────────────────────────────────────────────────────────────

-- Function to mark old SPAN entries as non-latest and enable new ones
CREATE OR REPLACE FUNCTION update_span_latest_flag(
    p_exchange VARCHAR,
    p_download_date DATE
) RETURNS void AS $$
BEGIN
    -- For NSE
    IF p_exchange = 'NSE' THEN
        UPDATE span_margin_cache
        SET is_latest = false
        WHERE exchange = 'NSE' 
          AND downloaded_at < p_download_date;
        
        UPDATE span_margin_cache
        SET is_latest = true
        WHERE exchange = 'NSE'
          AND downloaded_at = p_download_date;
    
    -- For MCX
    ELSIF p_exchange = 'MCX' THEN
        UPDATE mcx_span_margin_cache
        SET is_latest = false
        WHERE downloaded_at < p_download_date;
        
        UPDATE mcx_span_margin_cache
        SET is_latest = true
        WHERE downloaded_at = p_download_date;
    
    -- For BSE
    ELSIF p_exchange = 'BSE' THEN
        UPDATE bse_span_margin_cache
        SET is_latest = false
        WHERE downloaded_at < p_download_date;
        
        UPDATE bse_span_margin_cache
        SET is_latest = true
        WHERE downloaded_at = p_download_date;
    END IF;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION update_span_latest_flag IS 
'Marks old SPAN margin data as non-latest and enables new data for a given exchange/date';


-- Function to check if tomorrow is a trading day
CREATE OR REPLACE FUNCTION is_trading_day(
    p_date DATE,
    p_exchange VARCHAR DEFAULT 'NSE'
) RETURNS BOOLEAN AS $$
BEGIN
    -- Check if it's a weekend
    IF EXTRACT(DOW FROM p_date) IN (0, 6) THEN  -- 0=Sunday, 6=Saturday
        RETURN false;
    END IF;
    
    -- Check if it's in the holidays list
    IF EXISTS (
        SELECT 1 FROM exchange_holidays 
        WHERE exchange = p_exchange AND holiday_date = p_date
    ) THEN
        RETURN false;
    END IF;
    
    RETURN true;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION is_trading_day IS 
'Returns true if the given date is a trading day (not weekend, not holiday) for given exchange';


-- Function to get next trading day
CREATE OR REPLACE FUNCTION get_next_trading_day(
    p_date DATE,
    p_exchange VARCHAR DEFAULT 'NSE'
) RETURNS DATE AS $$
DECLARE
    next_date DATE := p_date + interval '1 day';
    max_date DATE := p_date + interval '7 days';
BEGIN
    WHILE next_date <= max_date LOOP
        IF is_trading_day(next_date, p_exchange) THEN
            RETURN next_date;
        END IF;
        next_date := next_date + interval '1 day';
    END LOOP;
    
    RETURN NULL;  -- No trading day found within 7 days
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_next_trading_day IS 
'Returns the next trading day after given date for specified exchange (up to 7 days)';


-- ────────────────────────────────────────────────────────────────────────────
-- 3.0: Initial data for 2026 holidays
-- ────────────────────────────────────────────────────────────────────────────

-- NSE/BSE Trading Holidays 2026 (extracted from nseindia.com)
INSERT INTO exchange_holidays (exchange, holiday_date, holiday_name) VALUES
('NSE', '2026-01-15', 'Municipal Corporation Election - Maharashtra'),
('NSE', '2026-01-26', 'Republic Day'),
('NSE', '2026-03-03', 'Holi'),
('NSE', '2026-03-26', 'Shri Ram Navami'),
('NSE', '2026-03-31', 'Shri Mahavir Jayanti'),
('NSE', '2026-04-03', 'Good Friday'),
('NSE', '2026-04-14', 'Dr. Baba Saheb Ambedkar Jayanti'),
('NSE', '2026-05-01', 'Maharashtra Day'),
('NSE', '2026-05-28', 'Bakri Id'),
('NSE', '2026-06-26', 'Muharram'),
('NSE', '2026-09-14', 'Ganesh Chaturthi'),
('NSE', '2026-10-02', 'Mahatma Gandhi Jayanti'),
('NSE', '2026-10-20', 'Dussehra'),
('NSE', '2026-11-10', 'Diwali-Balipratipada'),
('NSE', '2026-11-24', 'Prakash Gurpurb Sri Guru Nanak Dev'),
('NSE', '2026-12-25', 'Christmas'),
-- Note: Nov 08, 2026 (Sunday) has Muhurat trading extended hours, but is fundamentally a holiday
('NSE', '2026-11-08', 'Diwali Laxmi Pujan (Muhurat Trading)')
ON CONFLICT DO NOTHING;

-- BSE Holidays (typically same as NSE for derivatives)
INSERT INTO exchange_holidays (exchange, holiday_date, holiday_name) VALUES
('BSE', '2026-01-15', 'Municipal Corporation Election - Maharashtra'),
('BSE', '2026-01-26', 'Republic Day'),
('BSE', '2026-03-03', 'Holi'),
('BSE', '2026-03-26', 'Shri Ram Navami'),
('BSE', '2026-03-31', 'Shri Mahavir Jayanti'),
('BSE', '2026-04-03', 'Good Friday'),
('BSE', '2026-04-14', 'Dr. Baba Saheb Ambedkar Jayanti'),
('BSE', '2026-05-01', 'Maharashtra Day'),
('BSE', '2026-05-28', 'Bakri Id'),
('BSE', '2026-06-26', 'Muharram'),
('BSE', '2026-09-14', 'Ganesh Chaturthi'),
('BSE', '2026-10-02', 'Mahatma Gandhi Jayanti'),
('BSE', '2026-10-20', 'Dussehra'),
('BSE', '2026-11-10', 'Diwali-Balipratipada'),
('BSE', '2026-11-24', 'Prakash Gurpurb Sri Guru Nanak Dev'),
('BSE', '2026-12-25', 'Christmas'),
('BSE', '2026-11-08', 'Diwali Laxmi Pujan (Muhurat Trading)')
ON CONFLICT DO NOTHING;

-- MCX Trading Holidays 2026 (from mcxindia.com)
INSERT INTO exchange_holidays (exchange, holiday_date, holiday_name) VALUES
('MCX', '2026-01-01', 'New Year Day'),
('MCX', '2026-01-26', 'Republic Day'),
('MCX', '2026-03-03', 'Holi (2nd Day)'),
('MCX', '2026-03-26', 'Shri Ram Navami'),
('MCX', '2026-03-31', 'Shri Mahavir Jayanti'),
('MCX', '2026-04-03', 'Good Friday'),
('MCX', '2026-04-14', 'Dr. Baba Saheb Ambedkar Jayanti'),
('MCX', '2026-05-01', 'Maharashtra Day'),
('MCX', '2026-05-28', 'Bakri Id'),
('MCX', '2026-06-26', 'Moharram'),
('MCX', '2026-09-14', 'Ganesh Chaturthi'),
('MCX', '2026-10-02', 'Mahatma Gandhi Jayanti'),
('MCX', '2026-10-20', 'Dassera'),
('MCX', '2026-11-10', 'Diwali-Balipratipada'),
('MCX', '2026-11-24', 'Guru Nanak Jayanti'),
('MCX', '2026-12-25', 'Christmas')
ON CONFLICT DO NOTHING;

-- ────────────────────────────────────────────────────────────────────────────
-- 4.0: Summary
-- ────────────────────────────────────────────────────────────────────────────
--
-- Tables created:
--   - exchange_holidays: NSE, BSE, MCX trading holidays
--   - background_jobs: Job execution log for monitoring
--   - mcx_span_margin_cache: MCX commodity SPAN margins
--   - bse_span_margin_cache: BSE derivatives SPAN margins
--   - margin_download_logs: Audit trail for all margin downloads
--
-- Functions created:
--   - update_span_latest_flag: Mark old/new margin data
--   - is_trading_day: Check if date is trading day
--   - get_next_trading_day: Find next trading day
--
-- Columns added to existing tables:
--   - span_margin_cache.exchange: Now includes NSE, BSE, MCX
--   - elm_margin_cache.exchange: Now includes NSE, BSE, MCX
--
-- Initial data:
--   - 2026 holidays for NSE, BSE, MCX loaded from official sources
