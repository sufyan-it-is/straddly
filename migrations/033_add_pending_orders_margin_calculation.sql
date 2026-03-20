-- Migration 033: Add function to calculate margin required for pending orders
-- This ensures pending orders reserve margin and prevent over-leveraging

-- Drop existing function if any
DROP FUNCTION IF EXISTS calculate_pending_orders_margin(uuid);

-- Function to calculate total margin required for all pending orders
-- Only counts "fresh" orders that open new positions, not exit orders
CREATE OR REPLACE FUNCTION calculate_pending_orders_margin(p_user_id uuid)
RETURNS numeric
LANGUAGE plpgsql
AS $$
DECLARE
    v_total_margin numeric := 0;
    v_order_record RECORD;
    v_current_position_qty integer;
    v_is_fresh_order boolean;
    v_order_margin numeric;
    v_price numeric;
BEGIN
    -- Loop through all pending/open orders for the user
    FOR v_order_record IN 
        SELECT 
            po.id,
            po.symbol,
            po.exchange_segment,
            po.side,
            po.order_type,
            po.product_type,
            po.quantity,
            po.filled_quantity,
            po.price,
            po.limit_price,
            po.instrument_token
        FROM paper_orders po
        WHERE po.user_id = p_user_id
          AND po.status IN ('PENDING', 'OPEN', 'PARTIALLY_FILLED')
    LOOP
        -- Get current position quantity for this instrument
        SELECT COALESCE(SUM(pp.quantity), 0)
        INTO v_current_position_qty
        FROM paper_positions pp
        WHERE pp.user_id = p_user_id
          AND pp.instrument_token = v_order_record.instrument_token
          AND pp.status = 'OPEN';
        
        -- Calculate unfilled quantity
        DECLARE
            v_unfilled_qty integer;
        BEGIN
            v_unfilled_qty := v_order_record.quantity - COALESCE(v_order_record.filled_quantity, 0);
            
            -- Determine if this is a "fresh" order (opening position) vs exit order
            -- BUY orders:
            --   - If current position >= 0: fresh (opening long or adding to long)
            --   - If current position < 0 and ABS(current) >= unfilled: exit (closing short)
            --   - If current position < 0 and ABS(current) < unfilled: partial exit, partial fresh
            -- SELL orders:
            --   - If current position <= 0: fresh (opening short or adding to short)
            --   - If current position > 0 and current >= unfilled: exit (closing long)
            --   - If current position > 0 and current < unfilled: partial exit, partial fresh
            
            v_is_fresh_order := false;
            v_order_margin := 0;
            
            IF v_order_record.side = 'BUY' THEN
                IF v_current_position_qty >= 0 THEN
                    -- Opening or adding to long position - reserve full margin
                    v_is_fresh_order := true;
                ELSIF ABS(v_current_position_qty) < v_unfilled_qty THEN
                    -- Partially closing short, partially opening long
                    -- Only reserve margin for the fresh portion
                    v_unfilled_qty := v_unfilled_qty - ABS(v_current_position_qty);
                    v_is_fresh_order := true;
                ELSE
                    -- Fully closing short position - no margin needed
                    v_is_fresh_order := false;
                END IF;
            ELSIF v_order_record.side = 'SELL' THEN
                IF v_current_position_qty <= 0 THEN
                    -- Opening or adding to short position - reserve margin
                    v_is_fresh_order := true;
                ELSIF v_current_position_qty < v_unfilled_qty THEN
                    -- Partially closing long, partially opening short
                    v_unfilled_qty := v_unfilled_qty - v_current_position_qty;
                    v_is_fresh_order := true;
                ELSE
                    -- Fully closing long position - no margin needed
                    v_is_fresh_order := false;
                END IF;
            END IF;
            
            -- Calculate margin for fresh orders only
            IF v_is_fresh_order AND v_unfilled_qty > 0 THEN
                -- Determine price to use for margin calculation
                v_price := COALESCE(v_order_record.price, v_order_record.limit_price);
                
                IF v_price IS NULL OR v_price <= 0 THEN
                    -- For market orders or orders without price, we can't calculate exact margin
                    -- Skip margin reservation (will be checked at execution time)
                    CONTINUE;
                END IF;
                
                -- Call the existing position margin calculation function
                -- This uses SPAN/ELM data for F&O, premium for options, etc.
                v_order_margin := calculate_position_margin(
                    v_order_record.instrument_token,
                    v_order_record.symbol,
                    v_order_record.exchange_segment,
                    v_unfilled_qty,
                    v_order_record.product_type
                );
                
                v_total_margin := v_total_margin + COALESCE(v_order_margin, 0);
            END IF;
        END;
    END LOOP;
    
    RETURN COALESCE(v_total_margin, 0);
END;
$$;

-- Grant execute permission
GRANT EXECUTE ON FUNCTION calculate_pending_orders_margin(uuid) TO PUBLIC;

-- Test comment
COMMENT ON FUNCTION calculate_pending_orders_margin(uuid) IS 
'Calculates total margin required for pending orders. Only counts fresh orders that open new positions, not exit orders that close existing positions.';
