-- Migration 034: Fix pending-order margin function for status enum/text compatibility
-- Root cause: production uses PARTIAL status; comparing enum column against unknown
-- literals like PARTIALLY_FILLED can abort SQL transactions.

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
    v_unfilled_qty integer;
BEGIN
    FOR v_order_record IN
        SELECT
            po.order_id,
            po.symbol,
            po.exchange_segment,
            po.side,
            po.order_type,
            po.product_type,
            po.quantity,
            po.filled_qty,
            po.limit_price,
            po.fill_price,
            po.instrument_token
        FROM paper_orders po
        WHERE po.user_id = p_user_id
          AND po.status::text IN ('PENDING', 'OPEN', 'PARTIAL', 'PARTIAL_FILL', 'PARTIALLY_FILLED')
    LOOP
        SELECT COALESCE(SUM(pp.quantity), 0)
        INTO v_current_position_qty
        FROM paper_positions pp
        WHERE pp.user_id = p_user_id
          AND pp.instrument_token = v_order_record.instrument_token
          AND pp.status = 'OPEN';

        v_unfilled_qty := GREATEST(v_order_record.quantity - COALESCE(v_order_record.filled_qty, 0), 0);
        IF v_unfilled_qty <= 0 THEN
            CONTINUE;
        END IF;

        v_is_fresh_order := false;
        v_order_margin := 0;

        IF v_order_record.side = 'BUY' THEN
            IF v_current_position_qty >= 0 THEN
                v_is_fresh_order := true;
            ELSIF ABS(v_current_position_qty) < v_unfilled_qty THEN
                v_unfilled_qty := v_unfilled_qty - ABS(v_current_position_qty);
                v_is_fresh_order := true;
            END IF;
        ELSIF v_order_record.side = 'SELL' THEN
            IF v_current_position_qty <= 0 THEN
                v_is_fresh_order := true;
            ELSIF v_current_position_qty < v_unfilled_qty THEN
                v_unfilled_qty := v_unfilled_qty - v_current_position_qty;
                v_is_fresh_order := true;
            END IF;
        END IF;

        IF NOT v_is_fresh_order OR v_unfilled_qty <= 0 THEN
            CONTINUE;
        END IF;

        v_price := COALESCE(v_order_record.limit_price, v_order_record.fill_price);
        IF v_price IS NULL OR v_price <= 0 THEN
            CONTINUE;
        END IF;

        BEGIN
            v_order_margin := calculate_position_margin(
                v_order_record.instrument_token,
                v_order_record.symbol,
                v_order_record.exchange_segment,
                v_unfilled_qty,
                v_order_record.product_type
            );
            v_total_margin := v_total_margin + COALESCE(v_order_margin, 0);
        EXCEPTION WHEN OTHERS THEN
            -- Never break caller transaction for one malformed/edge order row.
            CONTINUE;
        END;
    END LOOP;

    RETURN COALESCE(v_total_margin, 0);
END;
$$;

COMMENT ON FUNCTION calculate_pending_orders_margin(uuid) IS
'Calculates reserved margin for pending fresh orders only. Uses status::text to avoid enum literal mismatch and avoids aborting caller transactions.';
