-- ============================================================
--  Straddly — Migration 043
--  Per-user TradingView chart storage/preferences
-- ============================================================

CREATE TABLE IF NOT EXISTS user_chart_layouts (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    symbol TEXT,
    resolution TEXT,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_chart_layouts_user_id
    ON user_chart_layouts (user_id);

CREATE INDEX IF NOT EXISTS idx_user_chart_layouts_user_updated
    ON user_chart_layouts (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS user_chart_study_templates (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_chart_study_templates_user_name UNIQUE (user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_user_chart_study_templates_user_id
    ON user_chart_study_templates (user_id);

CREATE TABLE IF NOT EXISTS user_chart_drawing_templates (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    template_name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_chart_drawing_templates UNIQUE (user_id, tool_name, template_name)
);

CREATE INDEX IF NOT EXISTS idx_user_chart_drawing_templates_user_tool
    ON user_chart_drawing_templates (user_id, tool_name);

CREATE TABLE IF NOT EXISTS user_chart_settings (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    setting_key TEXT NOT NULL,
    setting_value TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_user_chart_settings_user_key UNIQUE (user_id, setting_key)
);

CREATE INDEX IF NOT EXISTS idx_user_chart_settings_user_id
    ON user_chart_settings (user_id);
