-- Persist manual watchlist ordering for drag-and-drop UI.

ALTER TABLE watchlist_items
ADD COLUMN IF NOT EXISTS sort_index INTEGER;

WITH ranked AS (
    SELECT
        watchlist_id,
        instrument_token,
        ROW_NUMBER() OVER (
            PARTITION BY watchlist_id
            ORDER BY added_at DESC, instrument_token
        ) - 1 AS rn
    FROM watchlist_items
)
UPDATE watchlist_items wi
SET sort_index = ranked.rn
FROM ranked
WHERE wi.watchlist_id = ranked.watchlist_id
  AND wi.instrument_token = ranked.instrument_token
  AND wi.sort_index IS NULL;

CREATE INDEX IF NOT EXISTS idx_watchlist_items_order
ON watchlist_items (watchlist_id, sort_index, added_at DESC);
