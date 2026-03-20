-- Migration 021: Add order history and archival support
-- Allows orders to be hidden after end-of-day (4pm) cleanup
-- Archived orders are still accessible via admin historic orders page

ALTER TABLE paper_orders 
ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_po_archived_at ON paper_orders (archived_at) WHERE archived_at IS NULL;

-- View for current (non-archived) orders
CREATE OR REPLACE VIEW v_current_paper_orders AS
SELECT * FROM paper_orders 
WHERE archived_at IS NULL;

-- View for historic (archived) orders
CREATE OR REPLACE VIEW v_archived_paper_orders AS  
SELECT * FROM paper_orders
WHERE archived_at IS NOT NULL;
