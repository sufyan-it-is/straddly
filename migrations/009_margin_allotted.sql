-- Migration 009: Admin-allotted margin (separate from wallet balance)

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_accounts' AND column_name = 'margin_allotted'
    ) THEN
        ALTER TABLE paper_accounts
            ADD COLUMN margin_allotted NUMERIC(16,2) NOT NULL DEFAULT 0.00;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_pa_margin_allotted
    ON paper_accounts (margin_allotted);
