-- Migration 008: Payout requests

CREATE TABLE IF NOT EXISTS payout_requests (
    payout_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        NOT NULL,
    amount        NUMERIC(16,2) NOT NULL,
    mode          VARCHAR(20),
    status        VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at  TIMESTAMPTZ,
    note          TEXT,
    created_by    UUID,
    updated_by    UUID
);

CREATE INDEX IF NOT EXISTS idx_payout_user_time
    ON payout_requests (user_id, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_payout_status_time
    ON payout_requests (status, requested_at DESC);
