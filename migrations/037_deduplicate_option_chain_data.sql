-- ============================================================
-- 037  Deduplicate option_chain_data and add logical-key unique index
-- ============================================================
-- Root cause: instrument_master reloads can assign new instrument_token
-- values for the same (underlying, expiry_date, strike_price, option_type)
-- contract. The table's PK is instrument_token so both old and new rows
-- coexist, causing the API to serve whichever row Postgres returns first.
--
-- Fix (3 steps):
--   1. Remove orphan rows whose instrument_token is no longer in
--      instrument_master (typically mock/legacy tokens).
--   2. For any remaining duplicates on the logical key, keep the row
--      with the most recent greeks_updated_at and discard the rest.
--   3. Add a UNIQUE index on (underlying, expiry_date, strike_price,
--      option_type) so no future duplicate can enter the table.
-- ============================================================

-- Step 1: purge rows with unknown instrument_token
DELETE FROM option_chain_data
WHERE instrument_token NOT IN (
    SELECT instrument_token FROM instrument_master
);

-- Step 2: deduplicate on logical key — keep newest Greeks row
DELETE FROM option_chain_data
WHERE instrument_token IN (
    SELECT instrument_token
    FROM (
        SELECT
            instrument_token,
            ROW_NUMBER() OVER (
                PARTITION BY underlying, expiry_date, strike_price, option_type
                ORDER BY greeks_updated_at DESC NULLS LAST,
                         instrument_token DESC
            ) AS rn
        FROM option_chain_data
    ) ranked
    WHERE rn > 1
);

-- Step 3: enforce uniqueness at DB level so this can never happen again
CREATE UNIQUE INDEX IF NOT EXISTS idx_ocd_logical_key
    ON option_chain_data (underlying, expiry_date, strike_price, option_type);
