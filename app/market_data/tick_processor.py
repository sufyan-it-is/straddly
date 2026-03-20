"""
app/market_data/tick_processor.py
===================================
Receives raw tick dicts from WebSocket handlers, batches them,
and flushes to PostgreSQL every 100ms via a single UPSERT.

Also notifies:
  - ExecutionEngine.on_tick() for pending limit order checks
  - WebSocket push to subscribed frontend clients
"""
import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from app.database import get_pool
from app.config   import get_settings
from app.runtime.notifications import add_notification
from app.market_data.close_price_validator import validate_close_price
from app.market_hours import MarketState, get_market_state

log = logging.getLogger(__name__)
cfg = get_settings()


class _TickProcessor:
    """
    Buffer: instrument_token → latest tick dict.
    Latest tick wins within the batch window (no stale overwrites).
    """

    def __init__(self):
        # token → dict with latest fields
        self._buffer: dict[int, dict] = {}
        self._lock   = asyncio.Lock()
        self._task: asyncio.Task | None = None
        # Cache instrument metadata to avoid DB query per batch (TTL: 1 hour)
        self._meta_cache: dict[int, dict] = {}
        self._meta_cache_time: datetime | None = None
        self._meta_cache_ttl = timedelta(hours=1)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._task = asyncio.create_task(self._flush_loop(), name="tick-processor")
        log.info(f"Tick processor started (batch interval {cfg.tick_batch_ms}ms).")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            async with self._lock:
                self._buffer.clear()

    async def flush_now(self) -> None:
        """Force an immediate flush (admin diagnostics)."""
        if not self.is_running:
            return
        await self._flush()

    def clear_meta_cache(self) -> None:
        """Clear cached instrument metadata (used after instrument master refresh)."""
        self._meta_cache.clear()
        self._meta_cache_time = None
        log.info("Tick processor instrument metadata cache cleared.")

    async def push(self, tick: dict) -> None:
        """
        Called by WS handlers on every incoming tick.
        Merges into the buffer (latest values win).
        """
        if not self.is_running:
            return
        token = tick.get("instrument_token")
        if not token:
            return
        async with self._lock:
            if token in self._buffer:
                self._buffer[token].update({k: v for k, v in tick.items() if v is not None})
            else:
                self._buffer[token] = dict(tick)

    async def _flush_loop(self) -> None:
        interval = cfg.tick_batch_ms / 1000.0
        while True:
            await asyncio.sleep(interval)
            await self._flush()

    async def _flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            batch = list(self._buffer.values())
            self._buffer.clear()

        try:
            # Run all three operations in parallel instead of sequentially
            results = await asyncio.gather(
                self._upsert(batch),
                self._notify_execution_engine(batch),
                self._push_to_frontend(batch),
                return_exceptions=True,  # Don't fail entire batch if one operation fails
            )
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    op = ["upsert", "execution_engine", "frontend_push"][idx]
                    log.error("Tick processor %s failed: %s", op, result)
        except Exception as exc:
            log.error(f"Tick processor flush error: {exc}")
            try:
                await add_notification(
                    category="tick_processor",
                    severity="error",
                    title="Tick processor flush error",
                    message=str(exc),
                    dedupe_key="tick-flush-error",
                    dedupe_ttl_seconds=120,
                )
            except Exception:
                pass

    async def _upsert(self, batch: list[dict]) -> None:
        pool = get_pool()
        tokens = [t["instrument_token"] for t in batch]

        # Check if metadata cache is valid
        meta = {}
        refresh_cache = False
        if self._meta_cache_time is None or (datetime.utcnow() - self._meta_cache_time) > self._meta_cache_ttl:
            refresh_cache = True
        else:
            # Use cached metadata for tokens we have
            for t in tokens:
                if t in self._meta_cache:
                    meta[t] = self._meta_cache[t]

        # If we're missing some tokens or cache is stale, refresh
        missing_tokens = [t for t in tokens if t not in meta]
        if missing_tokens or refresh_cache:
            meta_rows = await pool.fetch(
                "SELECT instrument_token, symbol, exchange_segment "
                "FROM instrument_master WHERE instrument_token = ANY($1::bigint[])",
                tokens if refresh_cache else missing_tokens,
            )
            meta_dict = {r["instrument_token"]: r for r in meta_rows}
            if refresh_cache:
                self._meta_cache = meta_dict
                self._meta_cache_time = datetime.utcnow()
            else:
                meta.update(meta_dict)

        # Fetch existing close prices for validation
        existing_rows = await pool.fetch(
            "SELECT instrument_token, close, ltp "
            "FROM market_data WHERE instrument_token = ANY($1::bigint[])",
            tokens,
        )
        existing_data = {
            r["instrument_token"]: {
                "prev_close": float(r["close"]) if r["close"] else None,
                "prev_ltp": float(r["ltp"]) if r["ltp"] else None,
            }
            for r in existing_rows
        }

        rows = []
        for tick in batch:
            t   = tick["instrument_token"]
            m   = meta.get(t, {})
            sym = m.get("symbol") or tick.get("symbol")
            seg = m.get("exchange_segment") or tick.get("exchange_segment", "NSE_FNO")

            # Validate close price before storing
            close_price = tick.get("close")
            if close_price is not None:
                existing = existing_data.get(t, {})
                prev_close = existing.get("prev_close")
                current_ltp = tick.get("ltp")
                is_market_active = get_market_state(seg, sym or "") == MarketState.OPEN
                
                is_valid, _ = validate_close_price(
                    close_price=close_price,
                    instrument_token=t,
                    prev_close=prev_close,
                    ltp=current_ltp,
                    is_market_open=is_market_active,
                    symbol=sym or "UNKNOWN"
                )
                
                # If validation fails, skip this close price (set to None)
                if not is_valid:
                    close_price = None

            bid = json.dumps(tick.get("bid_depth")) if tick.get("bid_depth") else None
            ask = json.dumps(tick.get("ask_depth")) if tick.get("ask_depth") else None

            rows.append((
                t,
                seg,
                sym,
                tick.get("ltp"),
                tick.get("open"),
                tick.get("high"),
                tick.get("low"),
                close_price,  # Validated close price (or None if invalid)
                bid,
                ask,
                tick.get("ltt"),
            ))

        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO market_data
                    (instrument_token, exchange_segment, symbol,
                     ltp, open, high, low, close,
                     bid_depth, ask_depth, ltt, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,
                        $9::jsonb, $10::jsonb, $11, now())
                ON CONFLICT (instrument_token) DO UPDATE SET
                    exchange_segment = COALESCE(EXCLUDED.exchange_segment, market_data.exchange_segment),
                    symbol       = COALESCE(NULLIF(market_data.symbol, ''), EXCLUDED.symbol),
                    ltp          = COALESCE(EXCLUDED.ltp,          market_data.ltp),
                    open         = COALESCE(EXCLUDED.open,         market_data.open),
                    high         = COALESCE(EXCLUDED.high,         market_data.high),
                    low          = COALESCE(EXCLUDED.low,          market_data.low),
                    close        = COALESCE(EXCLUDED.close,        market_data.close),
                    bid_depth    = COALESCE(EXCLUDED.bid_depth,    market_data.bid_depth),
                    ask_depth    = COALESCE(EXCLUDED.ask_depth,    market_data.ask_depth),
                    ltt          = COALESCE(EXCLUDED.ltt,          market_data.ltt),
                    updated_at   = now()
                WHERE EXCLUDED.ltp          IS DISTINCT FROM market_data.ltp
                   OR EXCLUDED.bid_depth    IS DISTINCT FROM market_data.bid_depth
                   OR EXCLUDED.ask_depth    IS DISTINCT FROM market_data.ask_depth
                """,
                rows,
            )

    async def _notify_execution_engine(self, batch: list[dict]) -> None:
        """Notify execution engine on_tick for pending limit order checks."""
        # Execution engine exposes module-level functions (not a singleton object).
        from app.execution_simulator import execution_engine
        from app.execution_simulator.execution_config import get_tick_size
        from app.execution_simulator.order_queue_manager import pending_count

        # Fast-path: if no queued LIMIT/SL orders exist, skip per-tick checks entirely.
        # This avoids iterating every incoming tick through execution logic when idle.
        if pending_count() == 0:
            return

        for tick in batch:
            if tick.get("ltp") is None:
                continue
            seg = tick.get("exchange_segment") or "NSE_FNO"
            bid_depth = tick.get("bid_depth") or []
            ask_depth = tick.get("ask_depth") or []
            market_snap = {
                "ltp": tick.get("ltp"),
                "bid_depth": bid_depth[:5],
                "ask_depth": ask_depth[:5],
                "ltt": tick.get("ltt"),
                "tick_size": float(get_tick_size(seg)),
            }
            await execution_engine.on_tick(tick["instrument_token"], market_snap)

    async def _push_to_frontend(self, batch: list[dict]) -> None:
        """Push serialized ticks to subscribed frontend WebSocket clients."""
        from app.websocket_push import push_ticks
        await push_ticks(batch)


# Singleton
tick_processor = _TickProcessor()
