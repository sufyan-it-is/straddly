-- ============================================================
--  Straddly — Migration 039
--  Compliance-grade activity audit log + GeoIP/UA extensions
--  Retention: 7 years (2555 days) — append-only
-- ============================================================

-- ── Core activity audit log ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS activity_audit_log (
    id               BIGSERIAL       PRIMARY KEY,

    -- Who performed the action
    actor_user_id    UUID            REFERENCES users(id) ON DELETE SET NULL,
    actor_name       TEXT            NOT NULL DEFAULT '',
    actor_role       VARCHAR(30)     NOT NULL DEFAULT '',

    -- Who/what was affected (may be same as actor for self-service)
    subject_user_id  UUID            REFERENCES users(id) ON DELETE SET NULL,
    subject_name     TEXT            NOT NULL DEFAULT '',

    -- What happened
    action_type      VARCHAR(80)     NOT NULL,   -- LOGIN, LOGOUT, OTP_SEND, OTP_VERIFY_FAILED,
                                                 -- ENROLLMENT_SUBMIT, ACCOUNT_SIGNUP_SUBMIT,
                                                 -- SIGNUP_APPROVED, SIGNUP_REJECTED,
                                                 -- ADMIN_USER_CREATE, ADMIN_USER_UPDATE,
                                                 -- ADMIN_USER_ARCHIVE, etc.
    resource_type    VARCHAR(80)     NOT NULL DEFAULT '',
    resource_id      VARCHAR(200)    NOT NULL DEFAULT '',

    -- Request context
    endpoint         VARCHAR(255)    NOT NULL DEFAULT '',
    http_method      VARCHAR(10)     NOT NULL DEFAULT '',
    status_code      SMALLINT,
    error_detail     TEXT            NOT NULL DEFAULT '',

    -- Network identity
    ip_address       VARCHAR(45)     NOT NULL DEFAULT '',
    user_agent       VARCHAR(500)    NOT NULL DEFAULT '',

    -- Geolocation (city-level only, resolved offline at write time)
    geo_country      VARCHAR(100),
    geo_country_code VARCHAR(5),
    geo_region       VARCHAR(100),
    geo_city         VARCHAR(100),
    geo_latitude     NUMERIC(9,6),
    geo_longitude    NUMERIC(9,6),

    -- Arbitrary extra context (order ids, change diffs, etc.)
    metadata         JSONB,

    created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- ── Indexes ────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_aal_created_at
    ON activity_audit_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_aal_actor_time
    ON activity_audit_log (actor_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_aal_subject_time
    ON activity_audit_log (subject_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_aal_action_time
    ON activity_audit_log (action_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_aal_ip_time
    ON activity_audit_log (ip_address, created_at DESC)
    WHERE ip_address <> '';

-- ── Retention policy metadata ─────────────────────────────────────────────
-- This table records the intended retention period; actual purge is a manual /
-- scheduled maintenance task that honours this value.
CREATE TABLE IF NOT EXISTS audit_retention_policy (
    id             SERIAL       PRIMARY KEY,
    table_name     VARCHAR(100) NOT NULL UNIQUE,
    retention_days INTEGER      NOT NULL,
    notes          TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO audit_retention_policy (table_name, retention_days, notes)
VALUES
    ('activity_audit_log',       2555, '7-year compliance retention'),
    ('user_signup_review_log',   2555, '7-year compliance retention'),
    ('otp_verifications',          90, 'Short-lived operational records'),
    ('system_notifications',       90, 'Rolling admin notification window')
ON CONFLICT (table_name) DO UPDATE
    SET retention_days = EXCLUDED.retention_days,
        updated_at     = NOW();

-- ── Extend portal_users with user-agent + geo fields ─────────────────────
ALTER TABLE portal_users
    ADD COLUMN IF NOT EXISTS user_agent   TEXT         NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS geo_country  VARCHAR(100),
    ADD COLUMN IF NOT EXISTS geo_region   VARCHAR(100),
    ADD COLUMN IF NOT EXISTS geo_city     VARCHAR(100);

-- ── Extend user_signups with user-agent + geo fields ─────────────────────
ALTER TABLE user_signups
    ADD COLUMN IF NOT EXISTS user_agent   TEXT         NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS geo_country  VARCHAR(100),
    ADD COLUMN IF NOT EXISTS geo_region   VARCHAR(100),
    ADD COLUMN IF NOT EXISTS geo_city     VARCHAR(100);

-- ── Extend otp_verifications with user-agent ─────────────────────────────
ALTER TABLE otp_verifications
    ADD COLUMN IF NOT EXISTS user_agent   TEXT;

-- ── Extend user_sessions with IP + user-agent ────────────────────────────
ALTER TABLE user_sessions
    ADD COLUMN IF NOT EXISTS ip_address   VARCHAR(45),
    ADD COLUMN IF NOT EXISTS user_agent   TEXT;

CREATE INDEX IF NOT EXISTS idx_user_sessions_ip
    ON user_sessions (ip_address)
    WHERE ip_address IS NOT NULL;
