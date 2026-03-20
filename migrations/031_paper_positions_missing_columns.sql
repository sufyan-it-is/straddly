-- Migration 031: Add missing columns to paper_positions
-- ======================================================
-- Adds status, realized_pnl, and closed_at columns that code expects
-- but were never added in previous migrations.

-- Add status column (defaults to 'OPEN' for existing positions with qty != 0)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'status'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN status VARCHAR(20) DEFAULT 'OPEN';
        -- Backfill: closed positions have quantity = 0
        UPDATE paper_positions SET status = 'CLOSED' WHERE quantity = 0;
        UPDATE paper_positions SET status = 'OPEN' WHERE quantity != 0;
    END IF;
END $$;

-- Add realized_pnl column to track P&L when position closes
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'realized_pnl'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN realized_pnl NUMERIC(16,2) DEFAULT 0;
    END IF;
END $$;

-- Add closed_at timestamp (if not already there from migration 006)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'closed_at'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN closed_at TIMESTAMPTZ;
    END IF;
END $$;

-- Ensure the unique constraint exists (from migration 010) for multiple entries per day
CREATE UNIQUE INDEX IF NOT EXISTS idx_pp_unique_open_position
    ON paper_positions (user_id, instrument_token)
    WHERE status = 'OPEN';

-- Ensure the general query index exists
CREATE INDEX IF NOT EXISTS idx_pp_user_instrument
    ON paper_positions (user_id, instrument_token, status, closed_at);

-- Ensure position_id exists (added in migration 010)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'position_id'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN position_id UUID DEFAULT gen_random_uuid() UNIQUE;
    END IF;
END $$;
