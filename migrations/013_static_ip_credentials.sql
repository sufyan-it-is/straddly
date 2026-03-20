-- Migration: 013_static_ip_credentials.sql
-- Purpose: Seed static IP credential keys in system_config
-- Created: 2026-02-21

INSERT INTO system_config (key, value) VALUES
    ('dhan_static_client_id', ''),
    ('dhan_api_key',          ''),
    ('dhan_api_secret',       '')
ON CONFLICT (key) DO NOTHING;
