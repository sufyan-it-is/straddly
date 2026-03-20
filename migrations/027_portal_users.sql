-- ============================================================
--  Straddly — Migration 027
--  Portal Users table for educational signup form collection.
-- ============================================================

-- ── Create portal_users table ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portal_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    mobile VARCHAR(20),
    city VARCHAR(100),
    experience_level VARCHAR(100) NOT NULL,
    interest VARCHAR(100),
    learning_goal TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- ── Create indexes for portal_users ────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_portal_users_email ON portal_users(email);
CREATE INDEX IF NOT EXISTS idx_portal_users_created_at ON portal_users(created_at DESC);

-- ── Add comment documenting the table ──────────────────────────────────────
COMMENT ON TABLE portal_users IS 'Educational portal signup registrations from learn.straddly.pro';
COMMENT ON COLUMN portal_users.email IS 'Email address must be unique for each signup.';
COMMENT ON COLUMN portal_users.experience_level IS 'Self-reported trading experience (e.g., Beginner, Intermediate, Advanced).';
