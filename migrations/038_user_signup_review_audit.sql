CREATE TABLE IF NOT EXISTS user_signup_review_log (
    id BIGSERIAL PRIMARY KEY,
    signup_id UUID NOT NULL REFERENCES user_signups(id) ON DELETE CASCADE,
    action VARCHAR(20) NOT NULL,
    previous_status VARCHAR(20) NOT NULL,
    new_status VARCHAR(20) NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    actor_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    actor_name TEXT NOT NULL DEFAULT '',
    actor_mobile VARCHAR(20) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_signup_review_log_signup_id
    ON user_signup_review_log(signup_id);

CREATE INDEX IF NOT EXISTS idx_user_signup_review_log_created_at
    ON user_signup_review_log(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_signup_review_log_action
    ON user_signup_review_log(action);