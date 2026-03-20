"""app/positions/eod_archiver.py

Archives CLOSED positions and FILLED orders daily at 16:00 IST.

"Archive" here means: set archived_at timestamp.
- Current positions/orders list hides archived items.
- Historic pages continue to read archived items by date range.

This matches the product requirement: "clean Closed Positions list and Orders at 4pm".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from app.database import get_pool

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _next_4pm_ist(now_ist: datetime) -> datetime:
    target = datetime.combine(now_ist.date(), time(16, 0, 0), tzinfo=IST)
    if now_ist >= target:
        target = target + timedelta(days=1)
    return target


@dataclass
class ArchiveResult:
    cancelled_stale_orders: int
    archived_positions: int
    archived_orders: int


class EodClosedPositionArchiver:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_run_at: datetime | None = None
        self.last_run_result: ArchiveResult | None = None
        self.last_run_error: str | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="eod_closed_position_archiver")
        log.info("EOD closed-position archiver started (runs daily at 16:00 IST).")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5)
        except asyncio.TimeoutError:
            self._task.cancel()
        except Exception:
            pass
        log.info("EOD closed-position archiver stopped.")

    async def run_once(self) -> ArchiveResult:
        pool = get_pool()
        async with pool.acquire() as conn:
                        # cancel all still-active orders at EOD (including same-day orders)
                        # so nothing carries forward overnight in pending/open state
            cancel_status = await conn.execute(
                """
                UPDATE paper_orders
                SET status = 'CANCELLED',
                    updated_at = NOW(),
                    archived_at = COALESCE(archived_at, NOW())
                WHERE archived_at IS NULL
                                    AND DATE(placed_at AT TIME ZONE 'Asia/Kolkata') <= CURRENT_DATE
                  AND status::text IN ('PENDING', 'OPEN', 'PARTIAL', 'PARTIAL_FILL', 'PARTIALLY_FILLED')
                """
            )

            # archive all CLOSED positions that are not already archived
            pos_status = await conn.execute(
                """
                UPDATE paper_positions
                SET archived_at = NOW()
                WHERE status = 'CLOSED'
                  AND archived_at IS NULL
                """
            )
            # archive all FILLED/PARTIAL/REJECTED/CANCELLED orders that are not already archived
            # but only if they are from previous trading days (placed_at < today)
            ord_status = await conn.execute(
                """
                UPDATE paper_orders
                SET archived_at = NOW()
                WHERE status IN ('FILLED', 'PARTIAL', 'REJECTED', 'CANCELLED')
                  AND archived_at IS NULL
                  AND DATE(placed_at AT TIME ZONE 'Asia/Kolkata') < CURRENT_DATE
                """
            )
        
        # asyncpg returns e.g. "UPDATE 123"
        try:
            cancelled_stale_orders = int(str(cancel_status).split()[-1])
        except Exception:
            cancelled_stale_orders = 0

        try:
            archived_positions = int(str(pos_status).split()[-1])
        except Exception:
            archived_positions = 0
            
        try:
            archived_orders = int(str(ord_status).split()[-1])
        except Exception:
            archived_orders = 0
            
        return ArchiveResult(
            cancelled_stale_orders=cancelled_stale_orders,
            archived_positions=archived_positions,
            archived_orders=archived_orders,
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            now_ist = datetime.now(IST)
            run_at = _next_4pm_ist(now_ist)
            sleep_s = max(1.0, (run_at - now_ist).total_seconds())
            log.info(
                "EOD archiver: next run scheduled at %s (in %.0fs)",
                run_at.isoformat(),
                sleep_s,
            )

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=sleep_s)
                break
            except asyncio.TimeoutError:
                pass

            try:
                res = await self.run_once()
                self.last_run_at = datetime.now(IST)
                self.last_run_result = res
                self.last_run_error = None
                log.info(
                    "EOD archiver: cancelled %s stale active order(s), archived %s CLOSED position(s), archived %s order(s).",
                    res.cancelled_stale_orders,
                    res.archived_positions,
                    res.archived_orders,
                )
            except Exception as exc:
                self.last_run_at = datetime.now(IST)
                self.last_run_error = str(exc)
                log.exception("EOD archiver failed: %s", exc)


eod_closed_position_archiver = EodClosedPositionArchiver()
