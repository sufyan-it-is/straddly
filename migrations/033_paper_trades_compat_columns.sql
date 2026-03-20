-- Migration 033: paper_trades compatibility columns for new execution flow
-- Adds columns expected by current code and preserves backward compatibility
-- with old schema that uses quantity instead of fill_qty.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'paper_trades' AND column_name = 'exchange_segment'
    ) THEN
        ALTER TABLE paper_trades ADD COLUMN exchange_segment VARCHAR(20);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'paper_trades' AND column_name = 'fill_qty'
    ) THEN
        ALTER TABLE paper_trades ADD COLUMN fill_qty INTEGER;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'paper_trades' AND column_name = 'slippage'
    ) THEN
        ALTER TABLE paper_trades ADD COLUMN slippage NUMERIC(12,4) DEFAULT 0;
    END IF;
END $$;

-- Backfill fill_qty from legacy quantity for old rows.
UPDATE paper_trades
SET fill_qty = quantity
WHERE fill_qty IS NULL;

-- Ensure future inserts using fill_qty keep legacy quantity in sync.
CREATE OR REPLACE FUNCTION sync_paper_trades_qty()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.fill_qty IS NULL AND NEW.quantity IS NOT NULL THEN
        NEW.fill_qty := NEW.quantity;
    END IF;

    IF NEW.quantity IS NULL AND NEW.fill_qty IS NOT NULL THEN
        NEW.quantity := NEW.fill_qty;
    END IF;

    IF NEW.slippage IS NULL THEN
        NEW.slippage := 0;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_paper_trades_qty ON paper_trades;
CREATE TRIGGER trg_sync_paper_trades_qty
BEFORE INSERT OR UPDATE ON paper_trades
FOR EACH ROW
EXECUTE FUNCTION sync_paper_trades_qty();

-- Keep old NOT NULL quantity constraint safe for new insert paths.
ALTER TABLE paper_trades ALTER COLUMN quantity SET DEFAULT 0;

-- Helpful index for execution/trade history joins.
CREATE INDEX IF NOT EXISTS idx_pt_order_id ON paper_trades(order_id);