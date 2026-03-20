-- ============================================================
--  MIGRATION: 023_fix_seed_user_roles
--  Ensures seed users have correct roles (UPSERT)
-- ============================================================

-- Ensure pgcrypto extension is loaded
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Update or insert seed users with correct roles using UPSERT
INSERT INTO users (id, name, mobile, password_hash, role, first_name, last_name) VALUES
    (
        '00000000-0000-0000-0000-000000000001',
        'Super Admin',
        '9999999999',
        crypt('admin123', gen_salt('bf', 12)),
        'SUPER_ADMIN',
        'Super Admin',
        ''
    ),
    (
        '00000000-0000-0000-0000-000000000002',
        'Admin',
        '8888888888',
        crypt('admin123', gen_salt('bf', 12)),
        'ADMIN',
        'Admin',
        ''
    ),
    (
        '00000000-0000-0000-0000-000000000004',
        'Super User',
        '6666666666',
        crypt('super123', gen_salt('bf', 12)),
        'SUPER_USER',
        'Super User',
        ''
    ),
    (
        '00000000-0000-0000-0000-000000000003',
        'Trader 1',
        '7777777777',
        crypt('user123', gen_salt('bf', 12)),
        'USER',
        'Trader 1',
        ''
    )
ON CONFLICT (id) DO UPDATE SET
    role = EXCLUDED.role,
    name = EXCLUDED.name,
    first_name = EXCLUDED.first_name,
    password_hash = EXCLUDED.password_hash;

-- Also fix any existing users by mobile number
UPDATE users SET role = 'SUPER_ADMIN' WHERE mobile = '9999999999' AND role != 'SUPER_ADMIN';
UPDATE users SET role = 'ADMIN' WHERE mobile = '8888888888' AND role != 'ADMIN';
UPDATE users SET role = 'SUPER_USER' WHERE mobile = '6666666666' AND role != 'SUPER_USER';
UPDATE users SET role = 'USER' WHERE mobile = '7777777777' AND role != 'USER';

-- Ensure paper_accounts exist for seed users
INSERT INTO paper_accounts (user_id, display_name, balance)
SELECT u.id, u.name, 1000000.00
FROM users u
WHERE u.id IN (
    '00000000-0000-0000-0000-000000000001',
    '00000000-0000-0000-0000-000000000002',
    '00000000-0000-0000-0000-000000000003',
    '00000000-0000-0000-0000-000000000004'
)
ON CONFLICT (user_id) DO NOTHING;
