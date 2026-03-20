-- ============================================================
--  Straddly — Migration 036
--  Split course enrollments from user signups
-- ============================================================

ALTER TABLE portal_users
    ADD COLUMN IF NOT EXISTS ip_details TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS sms_verified BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS user_signups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    first_name VARCHAR(100) NOT NULL DEFAULT '',
    middle_name VARCHAR(100) NOT NULL DEFAULT '',
    last_name VARCHAR(100) NOT NULL DEFAULT '',
    email VARCHAR(255) NOT NULL,
    mobile VARCHAR(20) NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    city VARCHAR(100) NOT NULL DEFAULT '',
    state VARCHAR(100) NOT NULL DEFAULT '',
    country VARCHAR(100) NOT NULL DEFAULT 'India',
    pan_number VARCHAR(20) NOT NULL DEFAULT '',
    aadhar_number VARCHAR(30) NOT NULL DEFAULT '',
    pan_upload TEXT,
    aadhar_upload TEXT,
    bank_account_number VARCHAR(100) NOT NULL DEFAULT '',
    ifsc VARCHAR(20) NOT NULL DEFAULT '',
    upi_id VARCHAR(100) NOT NULL DEFAULT '',
    ip_details TEXT NOT NULL DEFAULT '',
    sms_verified BOOLEAN NOT NULL DEFAULT FALSE,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    reviewed_by UUID,
    reviewed_at TIMESTAMPTZ,
    rejection_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_signups_email ON user_signups(email);
CREATE INDEX IF NOT EXISTS idx_user_signups_mobile ON user_signups(mobile);
CREATE INDEX IF NOT EXISTS idx_user_signups_status ON user_signups(status);
CREATE INDEX IF NOT EXISTS idx_user_signups_created_at ON user_signups(created_at DESC);

WITH moved_rows AS (
    INSERT INTO user_signups (
        id, name, first_name, middle_name, last_name, email, mobile,
        address, city, state, country,
        pan_number, aadhar_number, pan_upload, aadhar_upload,
        bank_account_number, ifsc, upi_id,
        ip_details, sms_verified, email_verified,
        status, reviewed_by, reviewed_at, rejection_reason,
        created_at, updated_at
    )
    SELECT
        id,
        name,
        COALESCE(first_name, ''),
        COALESCE(middle_name, ''),
        COALESCE(last_name, ''),
        email,
        COALESCE(mobile, ''),
        COALESCE(address, ''),
        COALESCE(city, ''),
        COALESCE(state, ''),
        COALESCE(country, 'India'),
        COALESCE(pan_number, ''),
        COALESCE(aadhar_number, ''),
        pan_upload,
        aadhar_upload,
        COALESCE(bank_account_number, ''),
        COALESCE(ifsc, ''),
        COALESCE(upi_id, ''),
        COALESCE(ip_details, ''),
        COALESCE(otp_verified, FALSE),
        COALESCE(email_verified, FALSE),
        COALESCE(status, 'PENDING'),
        reviewed_by,
        reviewed_at,
        COALESCE(rejection_reason, ''),
        created_at,
        updated_at
    FROM portal_users
    WHERE COALESCE(pan_number, '') <> ''
       OR COALESCE(aadhar_number, '') <> ''
       OR COALESCE(bank_account_number, '') <> ''
       OR COALESCE(ifsc, '') <> ''
       OR COALESCE(upi_id, '') <> ''
       OR COALESCE(otp_verified, FALSE) = TRUE
       OR COALESCE(status, 'PENDING') IN ('APPROVED', 'REJECTED')
    ON CONFLICT (id) DO NOTHING
    RETURNING id
)
DELETE FROM portal_users
WHERE id IN (SELECT id FROM moved_rows);
