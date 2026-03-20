-- Migration 007: Ledger entries
-- Provides account statement rows for wallet movements.

CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id      BIGSERIAL     PRIMARY KEY,
    user_id       UUID          NOT NULL,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    description   TEXT          NOT NULL,
    debit         NUMERIC(16,2),
    credit        NUMERIC(16,2),
    balance_after NUMERIC(16,2) NOT NULL,
    created_by    UUID,
    ref_type      VARCHAR(40),
    ref_id        TEXT
);

CREATE INDEX IF NOT EXISTS idx_ledger_user_time
    ON ledger_entries (user_id, created_at DESC);
