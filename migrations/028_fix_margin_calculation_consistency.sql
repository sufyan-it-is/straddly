/**
 * MIGRATION - FIX MARGIN CALCULATION CONSISTENCY
 * 
 * Problem: Order placement uses real SPAN + Exposure margins,
 *          but used_margin calculation uses inconsistent methods.
 * 
 * Solution: Create a SQL function that calculates actual margins
 *           for all instrument types, then update all queries to use it.
 * 
 * Margin Calculation Logic:
 * ─────────────────────────
 * 1. Options BUY (positive qty):     Premium = Current Option Price × Quantity
 * 2. Options SELL (negative qty):    SPAN + Exposure from NSE SPAN cache (or 100% notional fallback)
 * 3. Futures:                        SPAN + Exposure from NSE SPAN cache (or 15% fallback)
 * 4. Commodities (MCX):              SPAN + Exposure from MCX SPAN cache (or 10% fallback)
 * 5. Equities (NSE_EQ/BSE_EQ):       Full Notional = Quantity × Current Price
 * 
 * Impact: Available margin will now be accurately calculated across
 *         the entire system and consistent with order placement checks.
 */

-- Create function to calculate real margin for an open position
CREATE OR REPLACE FUNCTION calculate_position_margin(
    p_instrument_token BIGINT,
    p_symbol VARCHAR,
    p_exchange_segment VARCHAR,
    p_quantity INTEGER,
    p_product_type VARCHAR
) RETURNS NUMERIC AS $$
DECLARE
    v_ltp NUMERIC;
    v_span_margin NUMERIC;
    v_exposure_margin NUMERIC;
    v_margin NUMERIC;
BEGIN
    -- Get current LTP
    SELECT COALESCE(ltp, 0) INTO v_ltp
    FROM market_data
    WHERE instrument_token = p_instrument_token
    LIMIT 1;
    
    -- If no LTP, cannot calculate margin (skip position)
    IF v_ltp IS NULL OR v_ltp = 0 THEN
        RETURN 0;
    END IF;
    
    -- Calculate based on instrument type
    
    -- CASE 1: OPTIONS (CALL/PUT) - Different rules for BUY vs SELL
    IF (p_exchange_segment ILIKE '%OPT%' 
        OR p_symbol ILIKE '%CE' 
        OR p_symbol ILIKE '%PE') THEN
        
        -- OPTION BUY (positive quantity): Premium = Current Option Price × Quantity
        IF p_quantity > 0 THEN
            RETURN ABS(p_quantity) * v_ltp;
        END IF;
        
        -- OPTION SELL (negative quantity): Use SPAN + Exposure Limit from NSE cache
        IF p_quantity < 0 THEN
            SELECT COALESCE(price_scan, 0) INTO v_span_margin
            FROM span_margin_cache
            WHERE symbol = p_symbol 
                AND is_latest = true
            LIMIT 1;
            
            IF v_span_margin > 0 THEN
                -- SPAN found: Calculate SPAN × Quantity + Exposure Limit
                SELECT COALESCE(exposure_limit_margin, 0) INTO v_exposure_margin
                FROM span_margin_cache
                WHERE symbol = p_symbol 
                    AND is_latest = true
                LIMIT 1;
                
                RETURN (v_span_margin * ABS(p_quantity)) + (v_exposure_margin * ABS(p_quantity));
            ELSE
                -- SPAN not found: Use 100% of notional as fallback for option sellers
                RETURN ABS(p_quantity) * v_ltp;
            END IF;
        END IF;
        
        -- Zero quantity (closed position)
        RETURN 0;
    END IF;
    
    -- CASE 2: Futures - Use SPAN from cache if available
    IF (p_exchange_segment ILIKE '%FUT%' 
        OR p_symbol ILIKE '%FUT%') THEN
        -- Try to get SPAN from cache first
        SELECT COALESCE(price_scan, 0) INTO v_span_margin
        FROM span_margin_cache
        WHERE symbol = p_symbol 
            AND is_latest = true
        LIMIT 1;
        
        IF v_span_margin > 0 THEN
            -- SPAN found: Calculate SPAN × Quantity + Exposure Limit
            SELECT COALESCE(exposure_limit_margin, 0) INTO v_exposure_margin
            FROM span_margin_cache
            WHERE symbol = p_symbol 
                AND is_latest = true
            LIMIT 1;
            
            RETURN (v_span_margin * ABS(p_quantity)) + (v_exposure_margin * ABS(p_quantity));
        ELSE
            -- SPAN not found: Use default 15% of notional (fallback)
            RETURN v_ltp * ABS(p_quantity) * 0.15;
        END IF;
    END IF;
    
    -- CASE 3: Commodities - Check MCX SPAN cache
    IF (p_exchange_segment ILIKE '%COMM%'
        OR p_exchange_segment ILIKE '%MCX%') THEN
        SELECT COALESCE(price_scan, 0) INTO v_span_margin
        FROM mcx_span_margin_cache
        WHERE symbol = p_symbol 
            AND is_latest = true
        LIMIT 1;
        
        IF v_span_margin > 0 THEN
            SELECT COALESCE(exposure_limit_margin, 0) INTO v_exposure_margin
            FROM mcx_span_margin_cache
            WHERE symbol = p_symbol 
                AND is_latest = true
            LIMIT 1;
            
            RETURN (v_span_margin * ABS(p_quantity)) + (v_exposure_margin * ABS(p_quantity));
        ELSE
            -- Fallback: 10% for commodities
            RETURN v_ltp * ABS(p_quantity) * 0.10;
        END IF;
    END IF;
    
    -- CASE 4: EQUITIES (NSE_EQ, BSE_EQ) - Full Notional Value (qty × price)
    -- Margin required = Quantity × Current Price (for both MIS and NORMAL)
    IF (p_exchange_segment ILIKE '%EQ%'
        OR p_exchange_segment ILIKE 'NSE'
        OR p_exchange_segment ILIKE 'BSE') THEN
        RETURN ABS(p_quantity) * v_ltp;
    END IF;
    
    -- Default fallback for unclassified instruments: Full notional value
    RETURN ABS(p_quantity) * v_ltp;
    
END;
$$ LANGUAGE plpgsql STABLE;


-- Create a view for easier querying of margin-aware positions
CREATE OR REPLACE VIEW v_positions_with_margin AS
SELECT
    pp.position_id,
    pp.user_id,
    pp.instrument_token,
    pp.symbol,
    pp.exchange_segment,
    pp.quantity,
    pp.avg_price,
    pp.product_type,
    pp.status,
    pp.opened_at,
    pp.closed_at,
    COALESCE(md.ltp, pp.avg_price) as current_ltp,
    COALESCE(md.ltp - pp.avg_price, 0) * pp.quantity as mtm,
    calculate_position_margin(
        pp.instrument_token,
        pp.symbol,
        pp.exchange_segment,
        pp.quantity,
        pp.product_type
    ) as required_margin
FROM paper_positions pp
LEFT JOIN market_data md ON md.instrument_token = pp.instrument_token
WHERE pp.status = 'OPEN' AND pp.quantity != 0;


-- Create a view for user margin summary using actual SPAN calculations
CREATE OR REPLACE VIEW v_user_margin_summary AS
SELECT
    pa.user_id,
    COALESCE(pa.balance, 0) as wallet_balance,
    COALESCE(pa.margin_allotted, 0) as margin_allotted,
    COALESCE(SUM(pwm.required_margin), 0) as used_margin_real,
    NULL as available_margin
FROM paper_accounts pa
LEFT JOIN v_positions_with_margin pwm ON pwm.user_id = pa.user_id
GROUP BY pa.user_id, pa.balance, pa.margin_allotted;


-- Add comment documenting the change
COMMENT ON FUNCTION calculate_position_margin(BIGINT, VARCHAR, VARCHAR, INTEGER, VARCHAR) IS
'Calculates the actual required margin for an open position using:
- Option BUY (qty > 0):   Premium = Current Option Price × Quantity
- Option SELL (qty < 0):  SPAN + Exposure from NSE SPAN cache (or 100% notional fallback)
- Futures:               SPAN + Exposure from NSE SPAN cache (or 15% fallback)
- Commodities:           SPAN from MCX SPAN cache (or 10% fallback)
- Equities:              Full Notional Value (Qty × Price) for both MIS and NORMAL

This ensures:
1. Correct margin for option sellers using SPAN + Exposure
2. Correct margin for option buyers using premium only
3. Consistency between order placement checks and used margin display
4. Proper enforcement of available margin limits';
