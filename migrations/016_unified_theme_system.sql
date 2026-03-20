-- ============================================================
--  Straddly — Migration 016 (Unified)
--  Complete theme system: user preferences + admin presets
--  
--  Combines original migrations 016, 017, and 018
--  - User theme preference (light/dark mode)
--  - Theme definitions table with admin-configurable presets
--  - Default neumorphic presets (Light-1, Light-Sky, Grey, Dark)
-- ============================================================

-- ============================================================
--  PART 1: Add per-user theme preference
-- ============================================================
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS theme_mode VARCHAR(10) NOT NULL DEFAULT 'dark';

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_theme_mode_check'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_theme_mode_check
            CHECK (theme_mode IN ('light', 'dark'));
    END IF;
END $$;

UPDATE users SET theme_mode = 'dark' WHERE theme_mode IS NULL;

-- ============================================================
--  PART 2: Create theme definitions table
-- ============================================================
CREATE TABLE IF NOT EXISTS ui_theme_definitions (
    id SERIAL PRIMARY KEY,
    preset_name VARCHAR(50) UNIQUE NOT NULL,
    mode VARCHAR(10) NOT NULL CHECK (mode IN ('light', 'dark')),
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_default BOOLEAN DEFAULT false,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE OR REPLACE FUNCTION update_ui_theme_definitions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'update_ui_theme_definitions_updated_at'
    ) THEN
        CREATE TRIGGER update_ui_theme_definitions_updated_at
            BEFORE UPDATE ON ui_theme_definitions
            FOR EACH ROW
            EXECUTE FUNCTION update_ui_theme_definitions_updated_at();
    END IF;
END $$;

-- ============================================================
--  PART 3: Populate theme definitions with neumorphic presets
-- ============================================================
BEGIN;

-- Clear any existing presets to avoid duplicates
DELETE FROM ui_theme_definitions;

-- Insert the 4 unified neumorphic presets
INSERT INTO ui_theme_definitions (preset_name, mode, config, is_default) VALUES

-- PRESET 1: Light-1 (Default light theme)
-- Base: #EBECF0, smooth elevation with deep shadows
('Light-1', 'light', '{
  "bg": "#EBECF0",
  "surface": "#EBECF0",
  "surface2": "#F3F4F7",
  "border": "#E0E3E8",
  "text": "#4A5568",
  "muted": "#8D96A8",
  "accent": "#185BF1",
  "nm_shadow": "#757085",
  "nm_highlight": "#FFFFFF",
  "light_source": "top-left",
  "shadow_distance": 7,
  "shadow_blur": 15,
  "light_shadow_intensity": 1.0,
  "dark_shadow_intensity": 0.15,
  "light_shadow_color": "#FFFFFF",
  "dark_shadow_color": "#3754AA",
  "pressed_inset": true
}', true),

-- PRESET 2: Light-Sky
-- Base: Sky blue tones with gradients and layered shadows
('Light-Sky', 'light', '{
  "bg": "#D8E4F0",
  "surface": "#FFFFFF",
  "surface2": "#EBF0F5",
  "border": "#C8DAE9",
  "text": "#2D3748",
  "muted": "#7FA3C7",
  "accent": "#6D5DFC",
  "nm_shadow": "#9BAACF",
  "nm_highlight": "#FFFFFF",
  "light_source": "top-left",
  "shadow_distance": 5,
  "shadow_blur": 10,
  "light_shadow_intensity": 1.0,
  "dark_shadow_intensity": 0.2,
  "light_shadow_color": "#FFFFFF",
  "dark_shadow_color": "#5B0EEB",
  "pressed_inset": true
}', false),

-- PRESET 3: Grey
-- Neutral professional grey tones
('Grey', 'light', '{
  "bg": "#D1D5DB",
  "surface": "#E5E7EB",
  "surface2": "#F3F4F6",
  "border": "#9CA3AF",
  "text": "#1F2937",
  "muted": "#6B7280",
  "accent": "#4B5563",
  "nm_shadow": "#9CA3AF",
  "nm_highlight": "#F9FAFB",
  "light_source": "top-left",
  "shadow_distance": 6,
  "shadow_blur": 12,
  "light_shadow_intensity": 0.9,
  "dark_shadow_intensity": 0.25,
  "light_shadow_color": "#FFFFFF",
  "dark_shadow_color": "#6B7280",
  "pressed_inset": true
}', false),

-- PRESET 4: Dark (Default dark theme)
-- Deep shadows for night mode
('Dark', 'dark', '{
  "bg": "#1A202C",
  "surface": "#2D3748",
  "surface2": "#374151",
  "border": "#4A5568",
  "text": "#F7FAFC",
  "muted": "#A0AEC0",
  "accent": "#63B3ED",
  "nm_shadow": "#0D1117",
  "nm_highlight": "#4A5568",
  "light_source": "top-left",
  "shadow_distance": 8,
  "shadow_blur": 16,
  "light_shadow_intensity": 0.3,
  "dark_shadow_intensity": 0.9,
  "light_shadow_color": "#545B6A",
  "dark_shadow_color": "#000000",
  "pressed_inset": true
}', true);

COMMIT;
