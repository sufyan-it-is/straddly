-- Migration 019: Archive (hide) closed positions after EOD cleanup
-- Purpose:
--   At EOD (4 PM IST) we mark CLOSED positions as archived so they no longer
--   appear in the Positions (open/closed) list, while still remaining available
--   for P&L historic reporting.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'archived_at'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pp_archived_at
    ON paper_positions (archived_at)
    WHERE status = 'CLOSED';
