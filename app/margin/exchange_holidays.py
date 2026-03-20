"""
app/margin/exchange_holidays.py
===============================
Holiday management for NSE, BSE, and MCX.

Features:
  - Downloads official trading holiday lists from exchange websites
  - Caches holidays in database (exchange_holidays table)
  - Updates on 1st trading day of each month at 08:00 IST
  - Provides is_trading_day() function using cached data
  - Fallback to hardcoded list if download fails

Holiday Sources:
  - NSE: https://www.nseindia.com/resources/exchange-communication-holidays
  - MCX: https://www.mcxindia.com/market-operations/trading-survelliance/trading-holidays
  - BSE: BSE derivatives follow NSE holidays (same trading days)
"""

import asyncio
import csv
import io
import logging
import re
from datetime import datetime, date, timedelta, timezone, time
from html.parser import HTMLParser
from typing import Optional, Set

import httpx

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# Import database pool
try:
    from app.database import get_pool
except ImportError:
    get_pool = None
    log.warning("Database not available for holiday caching")

# ────────────────────────────────────────────────────────────────────────────
# Holiday sources and parsers
# ────────────────────────────────────────────────────────────────────────────

_NSE_HOLIDAYS_URL = "https://www.nseindia.com/resources/exchange-communication-holidays"
_MCX_HOLIDAYS_URL = "https://www.mcxindia.com/market-operations/trading-survelliance/trading-holidays"

# Hardcoded fallback for 2026 if downloads fail
_FALLBACK_HOLIDAYS_2026 = {
    "NSE": [
        "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
        "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
        "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-08", "2026-11-10",
        "2026-11-24", "2026-12-25",
    ],
    "BSE": [  # Same as NSE for derivatives
        "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
        "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
        "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-08", "2026-11-10",
        "2026-11-24", "2026-12-25",
    ],
    "MCX": [
        "2026-01-01", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
        "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
        "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24",
        "2026-12-25",
    ],
}


class NSEHolidayParser(HTMLParser):
    """Parse NSE holiday table from HTML."""
    
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.cell_content = ""
        self.current_row = []
        self.holidays = []
        self.table_count = 0
    
    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table = True
            self.table_count += 1
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.cell_content = ""
    
    def handle_endtag(self, tag):
        if tag == "table" and self.in_table:
            self.in_table = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if len(self.current_row) >= 2:
                self.holidays.append(self.current_row)
        elif tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            self.current_row.append(self.cell_content.strip())
    
    def handle_data(self, data):
        if self.in_cell:
            self.cell_content += data


async def _download_nse_holidays(year: int) -> Optional[Set[str]]:
    """
    Download NSE trading holidays from official website.
    Returns set of date strings in 'YYYY-MM-DD' format.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(_NSE_HOLIDAYS_URL)
            if resp.status_code != 200:
                log.warning(f"NSE holiday page returned {resp.status_code}")
                return None
            
            parser = NSEHolidayParser()
            parser.feed(resp.text)
            
            holidays = set()
            for row in parser.holidays:
                if len(row) >= 2:
                    # Row format: "15-Jan-2026", "Thursday", "Municipal Corp..."
                    date_str = row[0].strip()
                    try:
                        # Parse date like "15-Jan-2026"
                        dt = datetime.strptime(date_str, "%d-%b-%Y")
                        if dt.year == year:
                            holidays.add(dt.strftime("%Y-%m-%d"))
                    except ValueError:
                        pass
            
            if holidays:
                log.info(f"Downloaded {len(holidays)} NSE holidays for {year}")
                return holidays
            else:
                log.warning("No holidays parsed from NSE page")
                return None
    
    except Exception as exc:
        log.error(f"Failed to download NSE holidays: {exc}")
        return None


async def _download_mcx_holidays(year: int) -> Optional[Set[str]]:
    """
    Download MCX trading holidays from official website.
    Returns set of date strings in 'YYYY-MM-DD' format.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(_MCX_HOLIDAYS_URL)
            if resp.status_code != 200:
                log.warning(f"MCX holiday page returned {resp.status_code}")
                return None
            
            # Parse MCX format: "JANUARY\n 1 NEW YEAR DAY (THU)\n 26 REPUBLIC DAY (MON)"
            # Extract month and date patterns
            holidays = set()
            text = resp.text
            
            months = [
                "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
                "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"
            ]
            
            current_month = None
            for month_idx, month_name in enumerate(months, 1):
                if month_name in text:
                    # Find the month section
                    start = text.find(month_name)
                    if month_idx < 12:
                        next_month_name = months[month_idx]
                        end = text.find(next_month_name, start)
                    else:
                        end = len(text)
                    
                    section = text[start:end]
                    
                    # Find date patterns like "1 NEW YEAR DAY" or "26 REPUBLIC DAY"
                    pattern = r'(\d{1,2})\s+([A-Z\s\-()]+)'
                    for match in re.finditer(pattern, section):
                        day = int(match.group(1))
                        if 1 <= day <= 31:
                            holiday_date = datetime(year, month_idx, day).strftime("%Y-%m-%d")
                            holidays.add(holiday_date)
            
            if holidays:
                log.info(f"Downloaded {len(holidays)} MCX holidays for {year}")
                return holidays
            else:
                log.warning("No holidays parsed from MCX page")
                return None
    
    except Exception as exc:
        log.error(f"Failed to download MCX holidays: {exc}")
        return None


async def sync_exchange_holidays(year: Optional[int] = None) -> bool:
    """
    Download and sync trading holidays for NSE, BSE, MCX from official sources.
    
    Args:
        year: Year to download (defaults to current year)
    
    Returns:
        True if at least one exchange was successfully synced, False if all failed
    """
    if year is None:
        year = datetime.now(tz=IST).year
    
    if not get_pool:
        log.warning("Database not available; cannot sync holidays")
        return False
    
    success_count = 0
    
    # Download NSE holidays
    nse_holidays = await _download_nse_holidays(year)
    if nse_holidays:
        await _save_holidays_to_db("NSE", nse_holidays)
        success_count += 1
    else:
        log.warning("Using fallback NSE holidays")
        fallback = _FALLBACK_HOLIDAYS_2026.get("NSE", [])
        await _save_holidays_to_db("NSE", set(fallback))
    
    # BSE has same holidays as NSE for derivatives
    if nse_holidays:
        await _save_holidays_to_db("BSE", nse_holidays)
        success_count += 1
    else:
        log.warning("Using fallback BSE holidays")
        fallback = _FALLBACK_HOLIDAYS_2026.get("BSE", [])
        await _save_holidays_to_db("BSE", set(fallback))
    
    # Download MCX holidays
    mcx_holidays = await _download_mcx_holidays(year)
    if mcx_holidays:
        await _save_holidays_to_db("MCX", mcx_holidays)
        success_count += 1
    else:
        log.warning("Using fallback MCX holidays")
        fallback = _FALLBACK_HOLIDAYS_2026.get("MCX", [])
        await _save_holidays_to_db("MCX", set(fallback))
    
    return success_count > 0


async def _save_holidays_to_db(exchange: str, holidays: Set[str]) -> None:
    """Save holidays to database, replacing old entries for the exchange."""
    if not get_pool:
        return
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            # Delete old entries for this exchange
            await conn.execute(
                "DELETE FROM exchange_holidays WHERE exchange = $1",
                exchange
            )
            
            # Insert new holidays
            rows = []
            for holiday_date_str in holidays:
                try:
                    dt = datetime.strptime(holiday_date_str, "%Y-%m-%d").date()
                    rows.append((exchange, dt, f"{exchange} Holiday"))
                except ValueError:
                    pass
            
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO exchange_holidays (exchange, holiday_date, holiday_name)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (exchange, holiday_date) DO NOTHING
                    """,
                    rows,
                )
                log.info(f"Saved {len(rows)} {exchange} holidays to database")
    
    except Exception as exc:
        log.error(f"Failed to save {exchange} holidays to database: {exc}")


async def is_trading_day(exchange: str, check_date: date) -> bool:
    """
    Check if the given date is a trading day for the specified exchange.
    
    Uses cached holiday data from database if available, otherwise uses database
    check via SQL.
    """
    # Check for weekend first (always non-trading)
    if check_date.weekday() >= 5:  # Sat=5, Sun=6
        return False
    
    if not get_pool:
        # Fallback to hardcoded holidays
        fallback = _FALLBACK_HOLIDAYS_2026.get(exchange, [])
        return check_date.strftime("%Y-%m-%d") not in fallback
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchval(
                """
                SELECT COUNT(*) FROM exchange_holidays
                WHERE exchange = $1 AND holiday_date = $2
                """,
                exchange,
                check_date,
            )
            return result == 0  # 0 rows = trading day
    
    except Exception as exc:
        log.error(f"Failed to check if {check_date} is trading day: {exc}")
        # Fallback
        fallback = _FALLBACK_HOLIDAYS_2026.get(exchange, [])
        return check_date.strftime("%Y-%m-%d") not in fallback


async def get_next_trading_day(
    exchange: str,
    start_date: Optional[date] = None,
    max_days: int = 7,
) -> Optional[date]:
    """
    Get the next trading day after start_date for the given exchange.
    
    Args:
        exchange: 'NSE', 'BSE', or 'MCX'
        start_date: Date to start from (defaults to today)
        max_days: Maximum days to search (default 7)
    
    Returns:
        Next trading date, or None if not found within max_days
    """
    if start_date is None:
        start_date = datetime.now(tz=IST).date()
    
    for days_offset in range(1, max_days + 1):
        candidate = start_date + timedelta(days=days_offset)
        is_trading = await is_trading_day(exchange, candidate)
        if is_trading:
            return candidate
    
    return None


async def load_holidays_into_memory() -> dict[str, set[date]]:
    """
    Load all holidays from database into memory for fast lookup.
    
    Returns:
        Dict mapping exchange name to set of holiday dates
    """
    if not get_pool:
        # Return fallback
        return {
            "NSE": {datetime.strptime(d, "%Y-%m-%d").date() for d in _FALLBACK_HOLIDAYS_2026["NSE"]},
            "BSE": {datetime.strptime(d, "%Y-%m-%d").date() for d in _FALLBACK_HOLIDAYS_2026["BSE"]},
            "MCX": {datetime.strptime(d, "%Y-%m-%d").date() for d in _FALLBACK_HOLIDAYS_2026["MCX"]},
        }
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT exchange, holiday_date FROM exchange_holidays")
            
            result = {"NSE": set(), "BSE": set(), "MCX": set()}
            for row in rows:
                exchange = row["exchange"]
                holiday_date = row["holiday_date"]
                if exchange in result:
                    result[exchange].add(holiday_date)
            
            log.info(f"Loaded {sum(len(v) for v in result.values())} holidays from database")
            return result
    
    except Exception as exc:
        log.error(f"Failed to load holidays from database: {exc}")
        # Return fallback
        return {
            "NSE": {datetime.strptime(d, "%Y-%m-%d").date() for d in _FALLBACK_HOLIDAYS_2026["NSE"]},
            "BSE": {datetime.strptime(d, "%Y-%m-%d").date() for d in _FALLBACK_HOLIDAYS_2026["BSE"]},
            "MCX": {datetime.strptime(d, "%Y-%m-%d").date() for d in _FALLBACK_HOLIDAYS_2026["MCX"]},
        }


# ────────────────────────────────────────────────────────────────────────────
# Scheduler integration
# ────────────────────────────────────────────────────────────────────────────

async def schedule_holiday_sync(
    schedule_next_at: Optional[datetime] = None,
) -> Optional[datetime]:
    """
    Schedule and execute holiday sync on 1st trading day of the month at 08:00 IST.
    
    Args:
        schedule_next_at: When to schedule the next sync (defaults to tomorrow at 08:00 IST)
    
    Returns:
        Datetime of next scheduled sync execution
    """
    if schedule_next_at is None:
        now = datetime.now(tz=IST)
        # Schedule for tomorrow at 08:00 IST
        next_sync = (now + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        schedule_next_at = next_sync
    
    # Check if it's a trading day (1st of month preferred)
    today = datetime.now(tz=IST).date()
    is_trading = await is_trading_day("NSE", schedule_next_at.date())
    
    if not is_trading:
        # Find next trading day
        next_trading = await get_next_trading_day("NSE", schedule_next_at.date())
        if next_trading:
            # Convert date to datetime at 08:00 IST
            schedule_next_at = datetime.combine(next_trading, time(8, 0, 0), tzinfo=IST)
    
    # Execute sync
    success = await sync_exchange_holidays(schedule_next_at.year)
    
    if success:
        log.info(f"Holiday sync completed successfully at {datetime.now(tz=IST)}")
    else:
        log.warning("Holiday sync completed with fallback data")
    
    # Schedule next sync for next month 1st trading day at 08:00 IST
    next_month_first = schedule_next_at.replace(day=1, month=(schedule_next_at.month % 12) + 1)
    if next_month_first.month == 1:
        next_month_first = next_month_first.replace(year=next_month_first.year + 1)
    
    next_first_trading = await get_next_trading_day("NSE", next_month_first.date())
    if next_first_trading:
        return datetime.combine(next_first_trading, time(8, 0, 0), tzinfo=IST)
    
    return None
