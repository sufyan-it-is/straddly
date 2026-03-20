-- Migration 020: Brokerage and Statutory Charges System
-- Adds comprehensive brokerage plans and charge tracking for positions

-- ============================================================
--  BROKERAGE PLANS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS brokerage_plans (
    plan_id           SERIAL        PRIMARY KEY,
    plan_code         VARCHAR(30)   NOT NULL UNIQUE, 
    plan_name         VARCHAR(100)  NOT NULL,
    instrument_group  VARCHAR(20)   NOT NULL,   -- 'EQUITY_OPTIONS' or 'FUTURES'
    flat_fee          NUMERIC(10,2) DEFAULT 20.0,
    percent_fee       NUMERIC(8,6)  DEFAULT 0.0,  -- 0.002 = 0.2%
    created_at        TIMESTAMPTZ   DEFAULT NOW(),
    updated_at        TIMESTAMPTZ   DEFAULT NOW(),
    is_active         BOOLEAN       DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_brokerage_plans_active ON brokerage_plans (is_active)  WHERE is_active = TRUE;

-- Insert predefined brokerage plans
INSERT INTO brokerage_plans (plan_code, plan_name, instrument_group, flat_fee, percent_fee) VALUES
    -- Equity & Options Plans
    ('PLAN_A',         'Plan A - Equity/Options - ₹20 flat',        'EQUITY_OPTIONS', 20.0, 0.0),
    ('PLAN_B',         'Plan B - Equity/Options - 0.2% turnover',   'EQUITY_OPTIONS', 0.0,  0.002),
    ('PLAN_C',         'Plan C - Equity/Options - 0.3% turnover',   'EQUITY_OPTIONS', 0.0,  0.003),
    ('PLAN_D',         'Plan D - Equity/Options - 0.4% turnover',   'EQUITY_OPTIONS', 0.0,  0.004),
    ('PLAN_E',         'Plan E - Equity/Options - 0.5% turnover',   'EQUITY_OPTIONS', 0.0,  0.005),
    -- Futures Plans
    ('PLAN_A_FUTURES', 'Plan A - Futures - ₹20 flat',               'FUTURES',        20.0, 0.0),
    ('PLAN_B_FUTURES', 'Plan B - Futures - 0.02% turnover',         'FUTURES',        0.0,  0.0002),
    ('PLAN_C_FUTURES', 'Plan C - Futures - 0.03% turnover',         'FUTURES',        0.0,  0.0003),
    ('PLAN_D_FUTURES', 'Plan D - Futures - 0.04% turnover',         'FUTURES',        0.0,  0.0004),
    ('PLAN_E_FUTURES', 'Plan E - Futures - 0.05% turnover',         'FUTURES',        0.0,  0.0005)
ON CONFLICT (plan_code) DO NOTHING;


-- ============================================================
--  ADD BROKERAGE PLAN FOREIGN KEYS TO USERS
-- ============================================================
DO $$
BEGIN
    -- Add equity/options plan reference
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'brokerage_plan_equity_id'
    ) THEN
        ALTER TABLE users ADD COLUMN brokerage_plan_equity_id INTEGER REFERENCES brokerage_plans(plan_id);
    END IF;
    
    -- Add futures plan reference
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'brokerage_plan_futures_id'
    ) THEN
        ALTER TABLE users ADD COLUMN brokerage_plan_futures_id INTEGER REFERENCES brokerage_plans(plan_id);
    END IF;
    
    -- Set default plans (PLAN_A for both)
    UPDATE users 
    SET brokerage_plan_equity_id = (SELECT plan_id FROM brokerage_plans WHERE plan_code = 'PLAN_A' LIMIT 1)
    WHERE brokerage_plan_equity_id IS NULL;
    
    UPDATE users 
    SET brokerage_plan_futures_id = (SELECT plan_id FROM brokerage_plans WHERE plan_code = 'PLAN_A_FUTURES' LIMIT 1)
    WHERE brokerage_plan_futures_id IS NULL;
END $$;


-- ============================================================
--  ADD CHARGE TRACKING FIELDS TO POSITIONS
-- ============================================================
DO $$
BEGIN
    -- Brokerage charges
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'brokerage_charge'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN brokerage_charge NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- STT/CTT charges
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'stt_ctt_charge'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN stt_ctt_charge NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- Exchange transaction charges
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'exchange_charge'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN exchange_charge NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- SEBI charges
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'sebi_charge'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN sebi_charge NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- Stamp duty
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'stamp_duty'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN stamp_duty NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- IPFT charges
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'ipft_charge'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN ipft_charge NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- GST
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'gst_charge'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN gst_charge NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- Total platform cost (brokerage + SEBI + exchange + GST on them)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'platform_cost'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN platform_cost NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- Total trade expense (STT + stamp duty + IPFT)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'trade_expense'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN trade_expense NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- Total charges (platform_cost + trade_expense)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'total_charges'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN total_charges NUMERIC(16,2) DEFAULT 0.0;
    END IF;
    
    -- Charges calculated flag
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'charges_calculated'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN charges_calculated BOOLEAN DEFAULT FALSE;
    END IF;
    
    -- Charges calculated timestamp
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_positions' AND column_name = 'charges_calculated_at'
    ) THEN
        ALTER TABLE paper_positions ADD COLUMN charges_calculated_at TIMESTAMPTZ;
    END IF;
END $$;

-- Create index for charge calculation queries
CREATE INDEX IF NOT EXISTS idx_pp_charges_pending
    ON paper_positions (status, charges_calculated, closed_at)
    WHERE status = 'CLOSED' AND charges_calculated = FALSE;


-- ============================================================
--  REMOVE OLD BROKERAGE_PLAN STRING COLUMN FROM USERS
-- ============================================================
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'brokerage_plan'
    ) THEN
        ALTER TABLE users DROP COLUMN brokerage_plan;
    END IF;
END $$;
