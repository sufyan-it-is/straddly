-- ============================================================
--  Straddly — Migration 005
--  Enhanced user profile, document storage, and status management.
-- ============================================================

-- ── Sequential display ID ─────────────────────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS users_user_no_seq START 1001;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS user_no BIGINT DEFAULT nextval('users_user_no_seq');

-- Back-fill user_no for existing rows (gives stable numbers to seed users)
UPDATE users SET user_no = nextval('users_user_no_seq') WHERE user_no IS NULL;

-- Make user_no unique and non-null going forward
ALTER TABLE users ALTER COLUMN user_no SET NOT NULL;
ALTER TABLE users ALTER COLUMN user_no SET DEFAULT nextval('users_user_no_seq');
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_user_no_unique') THEN
        ALTER TABLE users ADD CONSTRAINT users_user_no_unique UNIQUE (user_no);
    END IF;
END $$;

-- ── Profile fields ─────────────────────────────────────────────────────────
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS first_name           VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS last_name            VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS email                VARCHAR(200) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS address              TEXT         NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS country              VARCHAR(100) NOT NULL DEFAULT 'India',
    ADD COLUMN IF NOT EXISTS state                VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS city                 VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS aadhar_number        VARCHAR(30)  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS pan_number           VARCHAR(15)  NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS upi                  VARCHAR(100) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS bank_account         VARCHAR(200) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS brokerage_plan       VARCHAR(60)  NOT NULL DEFAULT 'Plan1 - 0.005×turnover',
    -- KYC documents stored as base64 strings
    ADD COLUMN IF NOT EXISTS aadhar_doc           TEXT,
    ADD COLUMN IF NOT EXISTS cancelled_cheque_doc TEXT,
    ADD COLUMN IF NOT EXISTS pan_card_doc         TEXT;

-- ── Status column (replaces boolean is_active) ────────────────────────────
--  PENDING   — newly created, not yet activated by admin
--  ACTIVE    — full trading access
--  SUSPENDED — can EXIT existing positions but cannot place new orders
--  BLOCKED   — read-only: can only view profile + make payout requests
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE';

-- Back-fill: inactive users → BLOCKED, active → ACTIVE
UPDATE users SET status = 'BLOCKED' WHERE is_active = FALSE AND status = 'ACTIVE';

-- Back-fill first_name from legacy name field
UPDATE users
SET first_name = name
WHERE first_name = '' AND name IS NOT NULL AND name <> '';
