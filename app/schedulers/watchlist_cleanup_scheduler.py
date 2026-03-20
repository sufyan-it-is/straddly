"""
app/schedulers/watchlist_cleanup_scheduler.py
=============================================
Daily cleanup of stale Tier-A watchlist entries.

Runs once at 06:30 IST every day — after the scrip master refresh (06:00)
and the Tier-B expiry rollover that follows it.

What it does
------------
Scans every user's watchlist and removes Tier-A (on-demand) instruments
that have NO open position.  Tier-B instruments are never touched.

Why 06:30 IST?
--------------
- Scrip master refreshes at 06:00 with today's contracts.
- The expiry rollover runs immediately after, evicting yesterday's expired
  contracts from the WS subscription map.
- At 06:30 the instrument_master is up-to-date and safe to query for tier.
- Market is still closed (opens 09:15), so no live trades can be affected.
"""
import asyncio
import logging
from datetime import datetime, time
from typing import Optional

import pytz

from app.database import get_pool

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
CLEANUP_TIME = time(hour=6, minute=30)   # 06:30 IST


class WatchlistCleanupScheduler:
    """
    Fires once daily at 06:30 IST.
    Removes Tier-A watchlist items (across ALL users) that have no open position.
    """

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._last_run: Optional[datetime] = None

    async def start(self) -> None:
        if self._running:
            logger.warning("WatchlistCleanupScheduler already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="watchlist_cleanup_scheduler")
        logger.info("WatchlistCleanupScheduler started — will clean at 06:30 IST daily.")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("WatchlistCleanupScheduler stopped.")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._check_and_run()
                await asyncio.sleep(300)   # check every 5 minutes
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"WatchlistCleanupScheduler loop error: {exc}")
                await asyncio.sleep(60)

    async def _check_and_run(self) -> None:
        now_ist   = datetime.now(IST)
        today     = now_ist.date()

        if (
            now_ist.time() >= CLEANUP_TIME
            and (self._last_run is None or self._last_run.date() < today)
        ):
            logger.info("WatchlistCleanupScheduler: running daily Tier-A watchlist cleanup…")
            result = await self.run_once()
            self._last_run = now_ist
            logger.info(
                f"WatchlistCleanupScheduler: done — "
                f"watchlists_scanned={result['watchlists_scanned']}, "
                f"items_removed={result['items_removed']}"
            )

    async def run_once(self) -> dict:
        """
        Remove all Tier-A watchlist entries (across all users) that have
        no open position.  Returns a stats dict.
        """
        pool = get_pool()

        # Fetch all watchlist IDs — one pass for all users.
        watchlist_ids = await pool.fetch("SELECT watchlist_id FROM watchlists")

        total_removed = 0
        scanned       = 0

        for row in watchlist_ids:
            wl_id   = row["watchlist_id"]
            scanned += 1

            stale = await pool.fetch(
                """
                SELECT wi.instrument_token
                FROM watchlist_items wi
                JOIN instrument_master im USING (instrument_token)
                WHERE wi.watchlist_id = $1
                  AND im.tier = 'A'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM paper_positions pp
                      WHERE pp.instrument_token = wi.instrument_token
                        AND pp.quantity != 0
                  )
                """,
                wl_id,
            )

            if not stale:
                continue

            tokens = [r["instrument_token"] for r in stale]
            await pool.execute(
                "DELETE FROM watchlist_items "
                "WHERE watchlist_id = $1 AND instrument_token = ANY($2::bigint[])",
                wl_id,
                tokens,
            )
            total_removed += len(tokens)
            logger.debug(
                f"Watchlist {wl_id}: removed {len(tokens)} stale Tier-A items."
            )

        return {
            "watchlists_scanned": scanned,
            "items_removed":      total_removed,
        }


watchlist_cleanup_scheduler = WatchlistCleanupScheduler()
