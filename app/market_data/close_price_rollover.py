"""
app/market_data/close_price_rollover.py
========================================
Daily rollover mechanism to update close prices at market open.

At 9:15 AM IST each trading day:
1. Update market_data.close = yesterday's last LTP (for equity)
2. Update option_chain_data.prev_close from REST API (already handled by greeks_poller)
3. Track rollover date to avoid duplicate updates

This ensures:
- Fresh close prices each day
- No stale historical prices
- Proper change% calculations
"""
import asyncio
import logging
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

from app.database import get_pool
from app.config import get_settings

log = logging.getLogger(__name__)
cfg = get_settings()

# Indian Standard Time
IST = ZoneInfo("Asia/Kolkata")

# Market open time (9:15 AM IST)
MARKET_OPEN_TIME = time(9, 15, 0)

# Rollover window (9:15 AM - 9:20 AM to allow some buffer)
ROLLOVER_WINDOW_END = time(9, 20, 0)


class _ClosePriceRollover:
    """
    Daily close price rollover at market open.
    
    Strategy:
    - At 9:15 AM IST, update market_data.close to previous LTP
    - This becomes the "previous session close" for today
    - Track rollover date in database to prevent duplicate updates
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._check_interval_seconds = 60  # Check every minute

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._task = asyncio.create_task(self._rollover_loop(), name="close-price-rollover")
        log.info("Close price rollover scheduler started.")

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

    async def force_rollover(self) -> dict:
        """Force rollover now (for admin/testing)."""
        log.info("Forcing close price rollover...")
        return await self._perform_rollover()

    async def _rollover_loop(self) -> None:
        """Check every minute if rollover should run."""
        while True:
            await asyncio.sleep(self._check_interval_seconds)
            
            try:
                now = datetime.now(IST)
                current_time = now.time()
                current_date = now.date()
                
                # Check if we're in rollover window (9:15 - 9:20 AM IST)
                if not (MARKET_OPEN_TIME <= current_time <= ROLLOVER_WINDOW_END):
                    continue
                
                # Check if rollover already done today
                if await self._is_rollover_done_today(current_date):
                    continue
                
                # Perform rollover
                log.info(f"Initiating daily close price rollover for {current_date}...")
                result = await self._perform_rollover()
                
                log.info(
                    f"Close price rollover completed: "
                    f"{result['updated_count']} instruments updated, "
                    f"{result['skipped_count']} skipped (no LTP)"
                )
                
            except Exception as exc:
                log.error(f"Close price rollover error: {exc}", exc_info=True)

    async def _is_rollover_done_today(self, check_date: date) -> bool:
        """Check if rollover already executed today."""
        pool = get_pool()
        
        # Check if a rollover tracking table exists
        # (We'll create this in the migration)
        try:
            row = await pool.fetchrow(
                """
                SELECT rollover_date 
                FROM close_price_rollover_log 
                WHERE rollover_date = $1 
                LIMIT 1
                """,
                check_date,
            )
            return row is not None
        except Exception:
            # Table doesn't exist yet, assume not done
            return False

    async def _perform_rollover(self) -> dict:
        """
        Perform the rollover:
        1. Update market_data.close = current LTP (yesterday's last price)
        2. Log the rollover
        """
        pool = get_pool()
        today = datetime.now(IST).date()
        
        async with pool.acquire() as conn:
            # Start transaction
            async with conn.transaction():
                # Update close prices: set close = current LTP (yesterday's last price)
                # Only update where LTP exists and is > 0
                update_result = await conn.execute(
                    """
                    UPDATE market_data
                    SET close = ltp,
                        updated_at = now()
                    WHERE ltp IS NOT NULL
                      AND ltp > 0
                      AND (close IS NULL OR close != ltp)
                    """
                )
                
                # Parse updated count from result string like "UPDATE 1234"
                updated_count = 0
                if update_result:
                    parts = update_result.split()
                    if len(parts) == 2 and parts[0] == "UPDATE":
                        updated_count = int(parts[1])
                
                # Count instruments that were skipped (no LTP)
                skip_result = await conn.fetchval(
                    """
                    SELECT COUNT(*) 
                    FROM market_data 
                    WHERE ltp IS NULL OR ltp <= 0
                    """
                )
                skipped_count = skip_result or 0
                
                # Log the rollover (create table if doesn't exist)
                try:
                    await conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS close_price_rollover_log (
                            rollover_date DATE PRIMARY KEY,
                            updated_count INTEGER NOT NULL,
                            skipped_count INTEGER NOT NULL,
                            executed_at TIMESTAMP NOT NULL DEFAULT now()
                        )
                        """
                    )
                    
                    await conn.execute(
                        """
                        INSERT INTO close_price_rollover_log 
                            (rollover_date, updated_count, skipped_count, executed_at)
                        VALUES ($1, $2, $3, now())
                        ON CONFLICT (rollover_date) DO UPDATE SET
                            updated_count = EXCLUDED.updated_count,
                            skipped_count = EXCLUDED.skipped_count,
                            executed_at = now()
                        """,
                        today,
                        updated_count,
                        skipped_count,
                    )
                except Exception as log_exc:
                    # Don't fail the rollover if logging fails
                    log.warning(f"Failed to log rollover: {log_exc}")
        
        return {
            "rollover_date": today,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
        }


# Singleton
close_price_rollover = _ClosePriceRollover()
