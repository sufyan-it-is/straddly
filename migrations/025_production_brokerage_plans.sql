--
-- Migration 025: Ensure Brokerage Plans Exist
-- Idempotent: uses ON CONFLICT DO NOTHING to handle duplicate key errors
-- This ensures the data is present even if run multiple times
--

-- Ensure brokerage plans exist (ignore if already present from migration 020)
INSERT INTO brokerage_plans (plan_id, plan_code, plan_name, instrument_group, flat_fee, percent_fee, is_active) VALUES
  (1, 'PLAN_A', 'Plan A - Equity/Options - ₹20 flat', 'EQUITY_OPTIONS', 20.00, 0.000000, true),
  (2, 'PLAN_B', 'Plan B - Equity/Options - 0.2% turnover', 'EQUITY_OPTIONS', 0.00, 0.002000, true),
  (3, 'PLAN_C', 'Plan C - Equity/Options - 0.3% turnover', 'EQUITY_OPTIONS', 0.00, 0.003000, true),
  (4, 'PLAN_D', 'Plan D - Equity/Options - 0.4% turnover', 'EQUITY_OPTIONS', 0.00, 0.004000, true),
  (5, 'PLAN_E', 'Plan E - Equity/Options - 0.5% turnover', 'EQUITY_OPTIONS', 0.00, 0.005000, true),
  (6, 'PLAN_A_FUTURES', 'Plan A - Futures - ₹20 flat', 'FUTURES', 20.00, 0.000000, true),
  (7, 'PLAN_B_FUTURES', 'Plan B - Futures - 0.02% turnover', 'FUTURES', 0.00, 0.000200, true),
  (8, 'PLAN_C_FUTURES', 'Plan C - Futures - 0.03% turnover', 'FUTURES', 0.00, 0.000300, true),
  (9, 'PLAN_D_FUTURES', 'Plan D - Futures - 0.04% turnover', 'FUTURES', 0.00, 0.000400, true),
  (10, 'PLAN_E_FUTURES', 'Plan E - Futures - 0.05% turnover', 'FUTURES', 0.00, 0.000500, true),
  (51, 'PLAN_NIL', 'Plan NIL - Equity/Options - ₹0 (no brokerage)', 'EQUITY_OPTIONS', 0.00, 0.000000, true),
  (52, 'PLAN_NIL_FUTURES', 'Plan NIL - Futures - ₹0 (no brokerage)', 'FUTURES', 0.00, 0.000000, true)
ON CONFLICT (plan_id) DO NOTHING;
