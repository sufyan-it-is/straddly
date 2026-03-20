-- ============================================================
--  Straddly — Migration 029
--  Add explicit security_id to instrument_master and backfill
--  from existing instrument_token values.
-- ============================================================

ALTER TABLE instrument_master
    ADD COLUMN IF NOT EXISTS security_id BIGINT;

UPDATE instrument_master
SET security_id = instrument_token
WHERE security_id IS NULL;

ALTER TABLE instrument_master
    ALTER COLUMN security_id SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'instrument_master_security_id_unique'
    ) THEN
        ALTER TABLE instrument_master
            ADD CONSTRAINT instrument_master_security_id_unique UNIQUE (security_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_im_security_id
    ON instrument_master (security_id);
