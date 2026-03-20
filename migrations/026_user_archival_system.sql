-- ============================================================
--  Straddly — Migration 026
--  User archival system for soft-delete functionality.
-- ============================================================

-- ── Add archival columns to users table ────────────────────────────────────
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMP WITH TIME ZONE;

-- ── Create index for archived users queries ────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_users_is_archived ON users(is_archived);
CREATE INDEX IF NOT EXISTS idx_users_archived_at ON users(archived_at DESC) WHERE is_archived = TRUE;

-- ── Add comment documenting soft delete behavior ────────────────────────────
COMMENT ON COLUMN users.is_archived IS 'TRUE when user is soft-deleted (archived). User cannot login but data is preserved.';
COMMENT ON COLUMN users.archived_at IS 'When the user was archived (soft-deleted).';
