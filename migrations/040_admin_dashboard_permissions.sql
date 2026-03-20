-- Per-admin access control for Admin Dashboard tabs.
-- NULL means "full default access" for backward compatibility.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS admin_tab_permissions TEXT[];
