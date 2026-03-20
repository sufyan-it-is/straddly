-- Migration 029: Close Price Rollover Log Table
-- ================================================
-- Created: 2026-02-25
-- Purpose: Track daily close price rollover executions
--
-- This table logs when the daily close price rollover has been executed
-- to prevent duplicate updates on the same trading day.

CREATE TABLE IF NOT EXISTS close_price_rollover_log (
    rollover_date  DATE PRIMARY KEY,
    updated_count  INTEGER NOT NULL,
    skipped_count  INTEGER NOT NULL,
    executed_at    TIMESTAMP NOT NULL DEFAULT now()
);

-- Index for quick date lookups (though PRIMARY KEY already provides this)
CREATE INDEX IF NOT EXISTS idx_rollover_log_date 
    ON close_price_rollover_log(rollover_date DESC);

-- Add helpful comment
COMMENT ON TABLE close_price_rollover_log IS 
    'Tracks daily close price rollover executions at market open (9:15 AM IST)';
COMMENT ON COLUMN close_price_rollover_log.rollover_date IS 
    'Trading date when rollover was executed';
COMMENT ON COLUMN close_price_rollover_log.updated_count IS 
    'Number of instruments with close price updated';
COMMENT ON COLUMN close_price_rollover_log.skipped_count IS 
    'Number of instruments skipped (no LTP available)';
COMMENT ON COLUMN close_price_rollover_log.executed_at IS 
    'Timestamp when rollover was executed';
