-- ============================================================
--  Straddly — Migration 002
--  Users, sessions, basket orders, and minor column additions
-- ============================================================

-- ============================================================
--  USERS  (trading desk members)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id            UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(100)  NOT NULL DEFAULT '',
    mobile        VARCHAR(15)   UNIQUE NOT NULL,
    password_hash TEXT          NOT NULL,
    role          VARCHAR(20)   NOT NULL DEFAULT 'USER',  -- USER | SUPER_USER | ADMIN | SUPER_ADMIN
    client_id     VARCHAR(100)  DEFAULT '',               -- optional DhanHQ client id mapping
    is_active     BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ   DEFAULT now()
);

-- ============================================================
--  USER SESSIONS  (simple token-based auth)
-- ============================================================
CREATE TABLE IF NOT EXISTS user_sessions (
    token      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID         NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ  DEFAULT now(),
    expires_at TIMESTAMPTZ  DEFAULT now() + INTERVAL '30 days'
);

CREATE INDEX IF NOT EXISTS idx_us_token   ON user_sessions (token);
CREATE INDEX IF NOT EXISTS idx_us_user_id ON user_sessions (user_id);

-- ============================================================
--  SEED DEFAULT USERS
--  Passwords are SHA-256 of the plain text.
--  default super_admin:  mobile=9999999999  password=admin123
--  default admin:        mobile=8888888888  password=admin123
--  default super_user:   mobile=6666666666  password=super123
--  default user:         mobile=7777777777  password=user123
-- ============================================================
INSERT INTO users (id, name, mobile, password_hash, role) VALUES
    (
        '00000000-0000-0000-0000-000000000001',
        'Super Admin',
        '9999999999',
        -- sha256('admin123')
        '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9',
        'SUPER_ADMIN'
    ),
    (
        '00000000-0000-0000-0000-000000000002',
        'Admin',
        '8888888888',
        -- sha256('admin123')
        '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9',
        'ADMIN'
    ),
    (
        '00000000-0000-0000-0000-000000000004',
        'Super User',
        '6666666666',
        -- sha256('super123')
        '4e4c56e4a15f89f05c2f4c72613da2a18c9665d4f0d6acce16415eb06f9be776',
        'SUPER_USER'
    ),
    (
        '00000000-0000-0000-0000-000000000003',
        'Trader 1',
        '7777777777',
        -- sha256('user123')
        '0f7e44a4c0cd43b760893e979ede7571eaabad4041b5ada32db00a7f1b60afad',
        'USER'
    )
ON CONFLICT (mobile) DO NOTHING;

-- Ensure each user has a paper_accounts row
INSERT INTO paper_accounts (user_id, display_name, balance)
    SELECT id, name, 1000000.00 FROM users
    ON CONFLICT (user_id) DO NOTHING;

-- ============================================================
--  BASKET ORDERS
-- ============================================================
CREATE TABLE IF NOT EXISTS basket_orders (
    id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID         NOT NULL,
    name       VARCHAR(100) NOT NULL DEFAULT 'My Basket',
    created_at TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bo_user_id ON basket_orders (user_id);

-- ============================================================
--  BASKET ORDER LEGS
-- ============================================================
CREATE TABLE IF NOT EXISTS basket_order_legs (
    id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    basket_id    UUID          NOT NULL REFERENCES basket_orders (id) ON DELETE CASCADE,
    symbol       VARCHAR(100),
    security_id  BIGINT,
    exchange     VARCHAR(20),
    side         VARCHAR(4),         -- BUY | SELL
    qty          INTEGER       NOT NULL DEFAULT 1,
    product_type VARCHAR(10)   DEFAULT 'MIS',
    price        NUMERIC(12,2) DEFAULT 0,
    order_type   VARCHAR(10)   DEFAULT 'MARKET',
    created_at   TIMESTAMPTZ   DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bol_basket_id ON basket_order_legs (basket_id);

-- ============================================================
--  paper_accounts — add user_id column alias so UUID users.id
--  can be used directly (ALTER only if column does not exist)
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_accounts' AND column_name = 'user_id'
    ) THEN
        ALTER TABLE paper_accounts ADD COLUMN user_id UUID;
    END IF;
END $$;

-- ============================================================
--  paper_orders — back-fill missing columns frontend expects
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_orders' AND column_name='transaction_type') THEN
        ALTER TABLE paper_orders ADD COLUMN transaction_type VARCHAR(4) GENERATED ALWAYS AS (side) STORED;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_orders' AND column_name='product_type') THEN
        ALTER TABLE paper_orders ADD COLUMN product_type VARCHAR(10) DEFAULT 'MIS';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_orders' AND column_name='security_id') THEN
        ALTER TABLE paper_orders ADD COLUMN security_id BIGINT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_orders' AND column_name='is_super') THEN
        ALTER TABLE paper_orders ADD COLUMN is_super BOOLEAN DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_orders' AND column_name='target_price') THEN
        ALTER TABLE paper_orders ADD COLUMN target_price NUMERIC(12,2);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_orders' AND column_name='stop_loss_price') THEN
        ALTER TABLE paper_orders ADD COLUMN stop_loss_price NUMERIC(12,2);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_orders' AND column_name='trailing_jump') THEN
        ALTER TABLE paper_orders ADD COLUMN trailing_jump NUMERIC(10,2);
    END IF;
END $$;

-- ============================================================
--  paper_positions — add mtm + status columns
-- ============================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_positions' AND column_name='mtm') THEN
        ALTER TABLE paper_positions ADD COLUMN mtm NUMERIC(16,2) DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_positions' AND column_name='status') THEN
        ALTER TABLE paper_positions ADD COLUMN status VARCHAR(10) DEFAULT 'OPEN';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_positions' AND column_name='realized_pnl') THEN
        ALTER TABLE paper_positions ADD COLUMN realized_pnl NUMERIC(16,2) DEFAULT 0;
    END IF;
    -- Rename net_qty → quantity if old name exists
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_positions' AND column_name='net_qty')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_positions' AND column_name='quantity') THEN
        ALTER TABLE paper_positions RENAME COLUMN net_qty TO quantity;
    END IF;
    -- Rename avg_cost → avg_price if old name exists
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_positions' AND column_name='avg_cost')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_positions' AND column_name='avg_price') THEN
        ALTER TABLE paper_positions RENAME COLUMN avg_cost TO avg_price;
    END IF;
    -- product_type column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='paper_positions' AND column_name='product_type') THEN
        ALTER TABLE paper_positions ADD COLUMN product_type VARCHAR(10) DEFAULT 'MIS';
    END IF;
END $$;
