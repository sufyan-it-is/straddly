-- ============================================================
--  SPAN Margin Data Cache Tables
--  Stores downloaded NSE SPAN and ELM data for persistence
--  across restarts and fallback when fresh download fails.
-- ============================================================

-- ============================================================
--  SPAN Margin Cache (from SPAN XML files)
-- ============================================================
CREATE TABLE IF NOT EXISTS span_margin_cache (
    symbol           VARCHAR(50)    NOT NULL,
    ref_price        NUMERIC(12,2)  NOT NULL,      -- underlying reference price
    price_scan       NUMERIC(12,2)  NOT NULL,      -- SPAN scan range (INR)
    cvf              NUMERIC(8,4)   NOT NULL DEFAULT 1.0,  -- contract value factor
    source           VARCHAR(10)    NOT NULL,      -- 'fo' or 'com'
    downloaded_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),
    is_latest        BOOLEAN        NOT NULL DEFAULT true,
    PRIMARY KEY (symbol, downloaded_at)
);

CREATE INDEX IF NOT EXISTS idx_span_latest ON span_margin_cache (is_latest) WHERE is_latest = true;
CREATE INDEX IF NOT EXISTS idx_span_symbol ON span_margin_cache (symbol);

-- ============================================================
--  ELM (Exposure Limit Margin) Cache (from AEL CSV)
-- ============================================================
CREATE TABLE IF NOT EXISTS elm_margin_cache (
    symbol             VARCHAR(50)    NOT NULL,
    instrument_type    VARCHAR(10)    NOT NULL,   -- 'OTH' (futures) or 'OTM' (options)
    normal_elm_pct     NUMERIC(8,4)   NOT NULL,
    additional_elm_pct NUMERIC(8,4)   NOT NULL,
    total_elm_pct      NUMERIC(8,4)   NOT NULL,
    downloaded_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),
    is_latest          BOOLEAN        NOT NULL DEFAULT true,
    PRIMARY KEY (symbol, instrument_type, downloaded_at)
);

CREATE INDEX IF NOT EXISTS idx_elm_latest ON elm_margin_cache (is_latest) WHERE is_latest = true;
CREATE INDEX IF NOT EXISTS idx_elm_symbol_type ON elm_margin_cache (symbol, instrument_type);

-- ============================================================
--  SPAN Download Log (tracks success/failure)
-- ============================================================
CREATE TABLE IF NOT EXISTS span_download_log (
    id               SERIAL         PRIMARY KEY,
    download_date    DATE           NOT NULL,      -- date attempted (NSE data date)
    attempted_at     TIMESTAMPTZ    NOT NULL DEFAULT now(),
    status           VARCHAR(20)    NOT NULL,      -- 'success' | 'failed' | 'fallback'
    span_symbols     INTEGER        DEFAULT 0,     -- count of SPAN symbols loaded
    elm_futures      INTEGER        DEFAULT 0,     -- count of ELM futures loaded
    elm_options      INTEGER        DEFAULT 0,     -- count of ELM options loaded
    error_message    TEXT,
    files_downloaded JSONB,                         -- {ael: true, fo_span: true, com_span: false}
    UNIQUE (download_date, attempted_at)
);

CREATE INDEX IF NOT EXISTS idx_download_log_date ON span_download_log (download_date DESC);
CREATE INDEX IF NOT EXISTS idx_download_log_status ON span_download_log (status, attempted_at DESC);

-- ============================================================
--  System Notifications (for admin dashboard)
-- ============================================================
CREATE TABLE IF NOT EXISTS system_notifications (
    id               SERIAL         PRIMARY KEY,
    category         VARCHAR(50)    NOT NULL,      -- 'span_download' | 'websocket' | 'auth' | 'order' etc.
    severity         VARCHAR(20)    NOT NULL DEFAULT 'info',  -- 'info' | 'warning' | 'error' | 'critical'
    title            VARCHAR(200)   NOT NULL,
    message          TEXT           NOT NULL,
    details          JSONB,                         -- additional context
    created_at       TIMESTAMPTZ    NOT NULL DEFAULT now(),
    read_at          TIMESTAMPTZ,
    acknowledged_by  UUID           REFERENCES users(id),
    acknowledged_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_notifications_severity ON system_notifications (severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON system_notifications (read_at) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_notifications_category ON system_notifications (category, created_at DESC);

-- ============================================================
--  Helper function to mark old SPAN/ELM data as not latest
-- ============================================================
CREATE OR REPLACE FUNCTION mark_span_data_as_old()
RETURNS TRIGGER AS $$
BEGIN
    -- When new SPAN data is inserted with is_latest=true, mark all old entries as not latest
    UPDATE span_margin_cache
    SET is_latest = false
    WHERE symbol = NEW.symbol
      AND downloaded_at < NEW.downloaded_at
      AND is_latest = true;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_span_latest_update'
    ) THEN
        CREATE TRIGGER trg_span_latest_update
            AFTER INSERT ON span_margin_cache
            FOR EACH ROW
            WHEN (NEW.is_latest = true)
            EXECUTE FUNCTION mark_span_data_as_old();
    END IF;
END $$;

-- ============================================================
--  Helper function to mark old ELM data as not latest
-- ============================================================
CREATE OR REPLACE FUNCTION mark_elm_data_as_old()
RETURNS TRIGGER AS $$
BEGIN
    -- When new ELM data is inserted with is_latest=true, mark all old entries as not latest
    UPDATE elm_margin_cache
    SET is_latest = false
    WHERE symbol = NEW.symbol
      AND instrument_type = NEW.instrument_type
      AND downloaded_at < NEW.downloaded_at
      AND is_latest = true;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_elm_latest_update'
    ) THEN
        CREATE TRIGGER trg_elm_latest_update
            AFTER INSERT ON elm_margin_cache
            FOR EACH ROW
            WHEN (NEW.is_latest = true)
            EXECUTE FUNCTION mark_elm_data_as_old();
    END IF;
END $$;

-- ============================================================
--  Comments
-- ============================================================
COMMENT ON TABLE span_margin_cache IS 'Persistent cache of NSE SPAN margin data from daily XML downloads';
COMMENT ON TABLE elm_margin_cache IS 'Persistent cache of NSE Exposure Limit Margin data from daily AEL CSV';
COMMENT ON TABLE span_download_log IS 'Audit log of SPAN data download attempts and results';
COMMENT ON TABLE system_notifications IS 'System-wide notifications for admin dashboard';

COMMENT ON COLUMN span_margin_cache.is_latest IS 'True for most recent download, false for historical data';
COMMENT ON COLUMN elm_margin_cache.is_latest IS 'True for most recent download, false for historical data';
COMMENT ON COLUMN span_download_log.status IS 'success = fresh download worked, failed = all downloads failed, fallback = used previous day data';
