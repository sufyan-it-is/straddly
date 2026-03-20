-- Migration 006: Add closed_at timestamp to paper_positions
-- This enables the EOD filter: closed positions only show on the day they were closed.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'closed_at'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN closed_at TIMESTAMPTZ;
    END IF;
END $$;

-- Back-fill: for already-closed positions set closed_at = opened_at
-- (best approximation since we don't know the real close time)
UPDATE paper_positions
SET closed_at = opened_at
WHERE status = 'CLOSED' AND closed_at IS NULL;

-- Index to make EOD filter efficient
CREATE INDEX IF NOT EXISTS idx_pp_closed_at
    ON paper_positions (closed_at)
    WHERE status = 'CLOSED';
