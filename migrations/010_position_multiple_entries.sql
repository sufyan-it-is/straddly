-- Migration 010: Allow multiple position entries per instrument per day
-- Fixes same-day re-entry issue where closing and re-opening overwrites position record

-- Step 1: Add position_id as surrogate primary key
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'position_id'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN position_id UUID DEFAULT gen_random_uuid();
    END IF;
END $$;

-- Step 2: Drop old primary key constraint
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'paper_positions' 
        AND constraint_type = 'PRIMARY KEY'
        AND constraint_name = 'paper_positions_pkey'
    ) THEN
        ALTER TABLE paper_positions DROP CONSTRAINT paper_positions_pkey;
    END IF;
END $$;

-- Step 3: Make position_id NOT NULL and set as primary key
ALTER TABLE paper_positions ALTER COLUMN position_id SET NOT NULL;
ALTER TABLE paper_positions ADD PRIMARY KEY (position_id);

-- Step 4: Create unique constraint to prevent multiple OPEN positions for same instrument
-- Note: This allows multiple CLOSED positions, enabling same-day re-entry tracking
CREATE UNIQUE INDEX IF NOT EXISTS idx_pp_unique_open_position
    ON paper_positions (user_id, instrument_token)
    WHERE status = 'OPEN';

-- Step 5: Create index for common queries
CREATE INDEX IF NOT EXISTS idx_pp_user_instrument
    ON paper_positions (user_id, instrument_token, status, closed_at);

-- Step 6: Back-fill position_id for any existing NULL values (shouldn't happen but safety)
UPDATE paper_positions
SET position_id = gen_random_uuid()
WHERE position_id IS NULL;

