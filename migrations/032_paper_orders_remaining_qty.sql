-- Migration 032: Add remaining_qty column to paper_orders
-- ===========================================================
-- Adds remaining_qty column to track unfilled quantity for partial fill monitoring.
-- This is required by the partial_fill_monitor.py system.

-- Add remaining_qty column (defaults to quantity for existing orders)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'paper_orders' AND column_name = 'remaining_qty'
    ) THEN
        ALTER TABLE paper_orders ADD COLUMN remaining_qty INTEGER;
        
        -- Backfill: For existing orders, set remaining_qty based on status
        -- FILLED orders have 0 remaining, PENDING/PARTIAL have quantity remaining
        UPDATE paper_orders SET remaining_qty = 0 WHERE status = 'FILLED';
        UPDATE paper_orders SET remaining_qty = 0 WHERE status = 'CANCELLED';
        UPDATE paper_orders SET remaining_qty = 0 WHERE status = 'REJECTED';
        UPDATE paper_orders SET remaining_qty = quantity WHERE status = 'PENDING';
        UPDATE paper_orders SET remaining_qty = quantity WHERE status = 'PARTIAL';
        
        -- For any orders with PENDING or PARTIAL status, calculate actual remaining
        -- by subtracting filled quantity from total quantity
        UPDATE paper_orders po
        SET remaining_qty = GREATEST(0, po.quantity - COALESCE(
            (SELECT SUM(quantity) FROM paper_trades pt 
             WHERE pt.order_id = po.order_id),
            0
        ))
        WHERE po.status IN ('PENDING', 'PARTIAL');
        
        -- Set default to quantity for any null values
        UPDATE paper_orders SET remaining_qty = quantity WHERE remaining_qty IS NULL;
        
        -- Make the column NOT NULL with default value for new rows
        ALTER TABLE paper_orders ALTER COLUMN remaining_qty SET DEFAULT 0;
        ALTER TABLE paper_orders ALTER COLUMN remaining_qty SET NOT NULL;
    END IF;
END $$;

-- Create index for partial fill monitor queries
CREATE INDEX IF NOT EXISTS idx_po_remaining_qty
    ON paper_orders (user_id, status, remaining_qty)
    WHERE status IN ('PENDING', 'PARTIAL') AND remaining_qty > 0;
