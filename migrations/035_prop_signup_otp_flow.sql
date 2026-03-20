-- ============================================================
--  Straddly — Migration 035
--  Prop signup workflow + OTP verification + IFSC column
-- ============================================================

-- Users: keep IFSC code for approved prop signups
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS ifsc_code VARCHAR(20) NOT NULL DEFAULT '';

-- Extend portal_users into a full prop-signup staging table
ALTER TABLE portal_users
    ADD COLUMN IF NOT EXISTS first_name VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS middle_name VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_name VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS address TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS state VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS country VARCHAR(100) NOT NULL DEFAULT 'India',
    ADD COLUMN IF NOT EXISTS pan_number VARCHAR(20) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS aadhar_number VARCHAR(30) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS pan_upload TEXT,
    ADD COLUMN IF NOT EXISTS aadhar_upload TEXT,
    ADD COLUMN IF NOT EXISTS bank_account_number VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS ifsc VARCHAR(20) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS upi_id VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS otp_verified BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    ADD COLUMN IF NOT EXISTS reviewed_by UUID,
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS rejection_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_portal_users_status ON portal_users(status);
CREATE INDEX IF NOT EXISTS idx_portal_users_mobile ON portal_users(mobile);

-- Keep name column in sync for old rows that had only name
UPDATE portal_users
SET first_name = name
WHERE COALESCE(first_name, '') = '' AND COALESCE(name, '') <> '';

-- OTP verification records
CREATE TABLE IF NOT EXISTS otp_verifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_type VARCHAR(20) NOT NULL,
    contact_value VARCHAR(120) NOT NULL,
    purpose VARCHAR(40) NOT NULL,
    otp_hash VARCHAR(128) NOT NULL,
    otp_salt VARCHAR(64) NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    request_ip VARCHAR(80),
    provider_response JSONB,
    verified_at TIMESTAMPTZ,
    consumed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_otp_lookup
ON otp_verifications (contact_type, contact_value, purpose, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_otp_expiry
ON otp_verifications (expires_at);
