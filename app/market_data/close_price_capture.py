"""
app/market_data/close_price_capture.py
========================================
Market close handler — captures final closing prices and updates ATM cache.

Runs daily at 3:46 PM IST (16 minutes after 3:30 PM market close, after 3:45 PM post-market settlement completes).

Responsibility:
1. Read closing prices from market_data table (populated via Dhan WS ticks)
2. Update atm_calculator cache with final closing prices
3. Fallback to Dhan REST API if index tokens lack closing ticks in DB
4. Log all updates for audit trail

This ensures:
- ATM cache is correctly calibrated for next trading day
- Closing prices are preserved in atm_calculator before day boundary
- Frontend receives accurate ATM when fetching /options/live after market close
"""
import asyncio
import logging
from datetime import datetime, time, date
from zoneinfo import ZoneInfo

from app.database import get_pool
from app.config import get_settings
from app.market_data.rate_limiter import dhan_client
from app.instruments.atm_calculator import update_atm, get_atm, set_atm
from app.market_data.index_underlyings import (
    IDX_SEG as _IDX_SEG,
    IDX_UNDERLYINGS,
    resolve_index_security_id,
)

log = logging.getLogger(__name__)
cfg = get_settings()

# Indian Standard Time
IST = ZoneInfo("Asia/Kolkata")

# Market close time: 3:30 PM, Post-market settlement: until 3:45 PM
# Schedule capture at 3:46 PM (after settlements complete for accuracy)
MARKET_CLOSE_CAPTURE_TIME = time(15, 46, 0)  # 3:46 PM IST

# Underlying configurations
_STRIKE_INTERVALS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "SENSEX": 100,
}


class _ClosePriceCapture:
    """
    Scheduler for market close price capture.
    
    Updates ATM cache at 3:46 PM IST (after post-market settlement window 3:30-3:45 PM)
    with final closing prices from:
    1. market_data table (via Dhan WebSocket ticks)
    2. Dhan REST API (fallback if index tokens lack WS subscriptions)
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._check_interval_seconds = 60  # Check every minute if time to capture

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._task = asyncio.create_task(self._capture_loop(), name="close-price-capture")
        log.info("Close price capture scheduler started (3:46 PM IST).")

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

    async def _capture_loop(self) -> None:
        """Check every minute if it's time to capture closing prices."""
        while True:
            await asyncio.sleep(self._check_interval_seconds)

            try:
                now = datetime.now(IST)
                current_time = now.time()
                current_date = now.date()

                # Check if we're at the capture window (3:46 PM ± 1 minute)
                # Runs after post-market settlement window (3:30-3:45 PM completes)
                capture_window_start = time(15, 45, 30)  # 3:45:30 PM
                capture_window_end = time(15, 46, 30)    # 3:46:30 PM

                if not (capture_window_start <= current_time <= capture_window_end):
                    continue

                # Check if already captured today
                if await self._is_captured_today(current_date):
                    continue

                log.info(f"🔵 Market close handler triggered at {current_time}…")
                result = await self._capture_closing_prices()

                # Log the result
                log.info(
                    f"🟢 Market close capture completed: "
                    f"{result['updated_count']} underlyings updated, "
                    f"{result['fallback_count']} fallbacks used, "
                    f"{result['error_count']} errors."
                )

                # Record captured date to prevent duplicates
                await self._mark_captured_today(current_date)

            except Exception as exc:
                log.error(f"🔴 Close price capture error: {exc}", exc_info=True)

    async def _is_captured_today(self, check_date: date) -> bool:
        """Check if closing prices were already captured today."""
        pool = get_pool()

        try:
            row = await pool.fetchrow(
                """
                SELECT capture_date 
                FROM close_price_capture_log 
                WHERE capture_date = $1 
                LIMIT 1
                """,
                check_date,
            )
            return row is not None
        except Exception:
            # Table doesn't exist yet, assume not captured
            return False

    async def _capture_closing_prices(self) -> dict:
        """
        Capture closing prices for all index underlyings.
        
        Returns:
            {
                "updated_count": int,  # Successfully updated from DB/Dhan
                "fallback_count": int,  # Used Dhan REST fallback
                "error_count": int,     # Failed to update
            }
        """
        updated_count = 0
        fallback_count = 0
        error_count = 0

        for underlying in _IDX_UNDERLYINGS:
            try:
                result = await self._update_atm_for_underlying(underlying)

                if result["status"] == "success":
                    updated_count += 1
                    source = "DB" if not result.get("used_fallback") else "Dhan REST"
                    log.info(
                        f"  ✅ {underlying}: ATM updated to {result['new_atm']} "
                        f"(LTP={result['ltp']}, source={source})"
                    )
                elif result["status"] == "fallback":
                    fallback_count += 1
                    log.warning(
                        f"  ⚠️  {underlying}: DB LTP unavailable, used Dhan REST fallback. "
                        f"ATM updated to {result['new_atm']} (LTP={result['ltp']})"
                    )
                else:
                    error_count += 1
                    log.error(
                        f"  ❌ {underlying}: {result['error']}"
                    )

            except Exception as exc:
                error_count += 1
                log.error(f"  ❌ {underlying}: Unexpected error: {exc}")

        return {
            "updated_count": updated_count,
            "fallback_count": fallback_count,
            "error_count": error_count,
        }

    async def _update_atm_for_underlying(self, underlying: str) -> dict:
        """
        Update ATM for a single underlying.
        
        Returns:
            {
                "status": "success" | "fallback" | "error",
                "new_atm": Decimal (if success/fallback),
                "ltp": float (if success/fallback),
                "used_fallback": bool,
                "error": str (if error),
            }
        """
        step = _STRIKE_INTERVALS.get(underlying, 100)

        # Try Method 1: Derive ATM from minimum CE+PE straddle (nearest expiry)
        straddle_atm = await self._get_atm_from_straddle_db(underlying)
        if straddle_atm is not None:
            try:
                new_atm = set_atm(underlying, straddle_atm)
                return {
                    "status": "success",
                    "new_atm": new_atm,
                    "ltp": straddle_atm,
                    "used_fallback": False,
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "error": f"Failed to set ATM from straddle minima: {exc}",
                }

        # Try Method 2: Query market_data table for latest LTP
        ltp = await self._get_ltp_from_market_data(underlying)

        if ltp is not None:
            try:
                new_atm = update_atm(underlying, ltp, step)
                return {
                    "status": "success",
                    "new_atm": new_atm,
                    "ltp": ltp,
                    "used_fallback": False,
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "error": f"Failed to update ATM from DB LTP: {exc}",
                }

        # Method 3: Fallback to Dhan REST API
        ltp = await self._get_ltp_from_dhan_rest(underlying)

        if ltp is not None:
            try:
                new_atm = update_atm(underlying, ltp, step)
                return {
                    "status": "fallback",
                    "new_atm": new_atm,
                    "ltp": ltp,
                    "used_fallback": True,
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "error": f"Failed to update ATM from Dhan REST LTP: {exc}",
                }

        # Both methods failed
        return {
            "status": "error",
            "error": "No closing price available from DB or Dhan REST API",
        }

    async def _get_atm_from_straddle_db(self, underlying: str) -> float | None:
        """Derive ATM as strike with minimum (CE+PE) for nearest available expiry."""
        pool = get_pool()
        try:
            row = await pool.fetchrow(
                """
                WITH nearest AS (
                    SELECT MIN(expiry_date) AS exp
                    FROM option_chain_data
                    WHERE underlying = $1
                      AND expiry_date >= CURRENT_DATE
                ),
                legs AS (
                    SELECT
                        ocd.strike_price,
                        ocd.option_type,
                        COALESCE(md.close, md.ltp, ocd.prev_close) AS px
                    FROM option_chain_data ocd
                    LEFT JOIN market_data md ON md.instrument_token = ocd.instrument_token
                    JOIN nearest n ON ocd.expiry_date = n.exp
                    WHERE ocd.underlying = $1
                )
                SELECT
                    strike_price,
                    MAX(CASE WHEN option_type = 'CE' THEN px END) AS ce_px,
                    MAX(CASE WHEN option_type = 'PE' THEN px END) AS pe_px
                FROM legs
                GROUP BY strike_price
                HAVING MAX(CASE WHEN option_type = 'CE' THEN px END) IS NOT NULL
                   AND MAX(CASE WHEN option_type = 'PE' THEN px END) IS NOT NULL
                ORDER BY (MAX(CASE WHEN option_type = 'CE' THEN px END)
                       +  MAX(CASE WHEN option_type = 'PE' THEN px END)) ASC,
                         strike_price ASC
                LIMIT 1
                """,
                underlying,
            )
            if not row or row["strike_price"] is None:
                return None
            return float(row["strike_price"])
        except Exception as exc:
            log.warning(f"{underlying}: Error deriving ATM from straddle DB data: {exc}")
            return None

    async def _get_ltp_from_market_data(self, underlying: str) -> float | None:
        """
        Query market_data table for the latest LTP of an index token.
        
        Index tokens receive Dhan WebSocket ticks during market hours.
        At market close, the final tick contains the closing price.
        """
        pool = get_pool()

        try:
            row = await pool.fetchrow(
                """
                SELECT md.ltp, md.updated_at
                FROM market_data md
                JOIN instrument_master im ON im.instrument_token = md.instrument_token
                WHERE im.symbol = $1 AND im.instrument_type = 'INDEX'
                ORDER BY md.updated_at DESC
                LIMIT 1
                """,
                underlying,
            )

            if row and row["ltp"] is not None and row["ltp"] > 0:
                return float(row["ltp"])

            log.debug(
                f"{underlying}: No valid LTP in market_data (may not have "
                f"received recent WebSocket ticks)"
            )
            return None

        except Exception as exc:
            log.warning(f"{underlying}: Error querying market_data: {exc}")
            return None

    async def _get_ltp_from_dhan_rest(self, underlying: str) -> float | None:
        """
        Fallback: Fetch current spot price from Dhan REST API.
        
        Calls POST /optionchain which returns:
          - last_price (current spot price, updated in real-time)
          - oc (option chain dict with calls/puts)
        
        This works even if index tokens don't have WS subscriptions or missed ticks.
        """
        security_id = await resolve_index_security_id(underlying)
        if not security_id:
            log.error(f"{underlying}: Unknown security ID")
            return None

        try:
            # Call Dhan option chain API (works with just spot price, expires irrelevant for this purpose)
            resp = await dhan_client.post(
                "/optionchain",
                json={
                    "UnderlyingScrip": security_id,
                    "UnderlyingSeg": _IDX_SEG,
                    "Expiry": datetime.now(IST).date().isoformat(),  # Today's date (dummy, not used)
                },
            )

            if resp.status_code != 200:
                log.warning(
                    f"{underlying}: Dhan /optionchain returned {resp.status_code}"
                )
                return None

            data = resp.json().get("data", {})
            last_price = data.get("last_price")

            if last_price is not None and last_price > 0:
                return float(last_price)

            log.warning(f"{underlying}: Dhan returned no last_price")
            return None

        except Exception as exc:
            log.error(f"{underlying}: Dhan REST API error: {exc}")
            return None

    async def _mark_captured_today(self, capture_date: date) -> None:
        """Record today's capture to prevent duplicate updates."""
        pool = get_pool()

        try:
            await pool.execute(
                """
                CREATE TABLE IF NOT EXISTS close_price_capture_log (
                    capture_date DATE PRIMARY KEY,
                    captured_at TIMESTAMP NOT NULL DEFAULT now()
                )
                """
            )

            await pool.execute(
                """
                INSERT INTO close_price_capture_log (capture_date, captured_at)
                VALUES ($1, now())
                ON CONFLICT (capture_date) DO NOTHING
                """,
                capture_date,
            )

        except Exception as exc:
            log.warning(f"Failed to log capture date: {exc}")


# Singleton instance
close_price_capture = _ClosePriceCapture()
