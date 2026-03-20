-- Migration 030: Add updated_at column to paper_orders
-- ======================================================
-- Created: 2026-03-05
-- Purpose: The code in execution_engine.py and orders.py references
--          updated_at on UPDATE statements, but the column was never
--          included in the original CREATE TABLE.  This caused:
--          UndefinedColumnError: column "updated_at" of relation
--          "paper_orders" does not exist
--          whenever an exit order (or cancel) was placed.

ALTER TABLE paper_orders
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
