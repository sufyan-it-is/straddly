-- Migration 041: Chart Candles Persistent Cache
-- ─────────────────────────────────────────────
-- Stores OHLCV candles fetched from DhanHQ so that:
--   • Chart data survives container restarts / page refreshes
--   • Only missing date ranges are re-fetched from DhanHQ
--   • Testing with a single instrument (e.g. RELIANCE) stays fast
--
-- Intervals stored exactly as the backend uses them:
--   '1m', '5m', '15m', '25m', '60m', 'D'   (upstream Dhan intervals)
--   '3m', '30m', '75m'                       (derived — aggregated by backend)

CREATE TABLE IF NOT EXISTS chart_candles (
    id               BIGSERIAL PRIMARY KEY,
    security_id      BIGINT       NOT NULL,
    exchange_segment TEXT         NOT NULL,
    interval         TEXT         NOT NULL,
    ts               BIGINT       NOT NULL,   -- epoch milliseconds
    open             DOUBLE PRECISION,
    high             DOUBLE PRECISION,
    low              DOUBLE PRECISION,
    close            DOUBLE PRECISION,
    volume           DOUBLE PRECISION,
    fetched_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT chart_candles_uq UNIQUE (security_id, exchange_segment, interval, ts)
);

-- Fast range-query index (the most common access pattern)
CREATE INDEX IF NOT EXISTS idx_chart_candles_range
    ON chart_candles (security_id, exchange_segment, interval, ts);

-- Track the earliest and latest timestamp we have per instrument+interval
-- so the seed/refresh logic can find gaps efficiently.
CREATE TABLE IF NOT EXISTS chart_candles_coverage (
    security_id      BIGINT  NOT NULL,
    exchange_segment TEXT    NOT NULL,
    interval         TEXT    NOT NULL,
    min_ts           BIGINT  NOT NULL,   -- epoch ms — oldest candle stored
    max_ts           BIGINT  NOT NULL,   -- epoch ms — newest candle stored
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chart_candles_coverage_uq UNIQUE (security_id, exchange_segment, interval)
);
