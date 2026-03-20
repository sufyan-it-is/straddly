-- ============================================================
--  Straddly — Migration 004
--  Rehash seed user passwords from SHA-256 → bcrypt.
--
--  Uses pgcrypto's crypt() / gen_salt() which produces standard
--  $2a$ bcrypt hashes fully compatible with Python's bcrypt.checkpw().
--
--  This migration only updates the four seeded accounts created in
--  migration 002.  Passwords of any real users already created via the
--  API are untouched because they were never stored as SHA-256 through
--  the new bcrypt-aware auth endpoint.
-- ============================================================

-- SUPER_ADMIN  mobile=9999999999  password=admin123
UPDATE users
SET    password_hash = crypt('admin123', gen_salt('bf', 12))
WHERE  mobile = '9999999999';

-- ADMIN        mobile=8888888888  password=admin123
UPDATE users
SET    password_hash = crypt('admin123', gen_salt('bf', 12))
WHERE  mobile = '8888888888';

-- SUPER_USER   mobile=6666666666  password=super123
UPDATE users
SET    password_hash = crypt('super123', gen_salt('bf', 12))
WHERE  mobile = '6666666666';

-- USER         mobile=7777777777  password=user123
UPDATE users
SET    password_hash = crypt('user123', gen_salt('bf', 12))
WHERE  mobile = '7777777777';
