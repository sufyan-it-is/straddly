-- Migration 023: Ensure brokerage_plan column exists
--  (fixing missing column from migration 005)

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'brokerage_plan'
    ) THEN
        ALTER TABLE users 
            ADD COLUMN brokerage_plan VARCHAR(60) NOT NULL DEFAULT 'Standard Plan';
    END IF;
END $$;
