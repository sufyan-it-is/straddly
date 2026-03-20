"""
app/margin/mcx_margin_data.py
=============================
Downloads and parses MCX (Multi-Commodity Exchange) SPAN margin data daily.

MCX Files downloaded:
  - Daily SPAN Risk Parameter Files from: 
    https://www.mcxccl.com/risk-management/daily-span-risk-parameter-file
  - File pattern: mcxrpf-{YYYYMMDD}-{HHMM}-{SEQUENCE}-i.zip
  - Multiple files per day: Begin Day (05:06 IST) + Intra-day updates (09:30, 11:00, 12:00)
  - We use the Begin Day file (first one after midnight)

MCX file format: ZIP containing SPAN Risk Parameter Files
  - Public download, no authentication required
  - Contains margin data for all commodity contracts
  - Updated daily and intra-day
  
Margin Formula (same as NSE):
  span_margin     = price_scan × quantity / cvf    [for commodity multipliers]
  exposure_margin = ref_price × quantity × elm_pct / 100
  ─────────────────────────────────────────────────────
  Total (seller/futures) = span_margin + exposure_margin
"""

import asyncio
import csv
import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional

import httpx

log = logging.getLogger(__name__)

# Import database pool for persistence
try:
    from app.database import get_pool
except ImportError:
    get_pool = None
    log.warning("Database not available for MCX margin persistence")

# Import exchange holidays
try:
    from app.margin.exchange_holidays import is_trading_day, get_next_trading_day
except ImportError:
    is_trading_day = None
    get_next_trading_day = None
    log.warning("Exchange holidays module not available")

IST = timezone(timedelta(hours=5, minutes=30))

# ────────────────────────────────────────────────────────────────────────────
# MCX Archive URL Templates
# ────────────────────────────────────────────────────────────────────────────

# MCX SPAN Risk Parameter Files (public download, no auth required)
# Base URL for daily files from MCX Clearing Corporation
_MCX_RPF_BASE = "https://www.mcxccl.com/risk-management/daily-span-risk-parameter-file"

# File pattern: {YYYY}/{month_name}/mcxrpf-{YYYYMMDD}-{HHMM}-{SEQUENCE}-i.zip
# Example: 2026/february/mcxrpf-20260220-0506-01-i.zip (Begin Day file at 05:06 IST)
# We prefer the Begin Day file (SEQUENCE=01, downloaded around 05:06 IST) which is the primary SPAN file

# Intra-day files available at:
# - 09:30 IST (SEQUENCE=02)
# - 11:00 IST (SEQUENCE=03)  
# - 12:00 IST (SEQUENCE=04)
# For this system, we only download Begin Day file to match NSE's 08:45 download strategy

# Contract value factors for major MCX commodities
_MCX_CVF = {
    "GOLD": 100,        # grams
    "GOLDM": 100,
    "GOLDGUINEA": 1,
    "GOLDPETAL": 1,
    "GOLDTEN": 1,
    "SILVER": 30000,    # grams
    "SILVERM": 30000,
    "SILVERMIC": 30000,
    "CRUDEOIL": 100,    # barrels
    "CRUDEOILM": 100,
    "NATURALGAS": 10,   # MMBtu
    "NATGASMINI": 10,
    "COPPER": 250,      # kg
    "COPPERM": 250,
    "ZINC": 250,
    "ZINCM": 250,
    "NICKEL": 100,      # kg
    "NICKELM": 100,
    "LEAD": 1000,       # kg
    "LEADM": 1000,
    "PEPPER": 1000,     # kg
    "RUBBER": 100,      # kg
    "MENTHOL": 100,     # kg
    "TURMERIC": 1000,   # kg
    "COTTONM": 100,     # bales
    "COTTON": 100,
    "COREGULD": 100,    # grams (core commodity)
}


@dataclass
class MCXMarginEntry:
    """Single MCX commodity SPAN margin data."""
    symbol: str
    ref_price: float
    price_scan: float
    cvf: float
    elm_pct: float


# In-memory cache of MCX margins
class _MCXStore:
    def __init__(self):
        self.margins: dict[str, MCXMarginEntry] = {}
        self.last_download: Optional[date_type] = None
        self.fallback_used: bool = False


_mcx_store = _MCXStore()


async def download_and_refresh(
    target_date: Optional[date_type] = None,
) -> dict:
    """
    Download MCX SPAN margin data for the given date.
    
    Strategy:
      1. If target_date is a holiday: try NEXT trading day first (future data)
      2. Then try: target_date, yesterday, 2 days back, 3 days back
      3. Fallback to database cache perpetually (previous day's data)
    
    Args:
        target_date: Date to download (defaults to today IST)
    
    Returns:
        dict with status, symbol_count, elm_pct, download_date
    """
    if target_date is None:
        target_date = datetime.now(tz=IST).date()
    
    log.info(f"MCX margin download starting for {target_date}")
    
    # Check if today is a holiday; if so, prefer next trading day
    if is_trading_day and get_next_trading_day:
        is_trading = await is_trading_day("MCX", target_date)
        if not is_trading:
            log.info(f"{target_date} is MCX holiday; attempting next trading day")
            next_trading = await get_next_trading_day("MCX", target_date)
            if next_trading:
                log.info(f"Trying next trading day: {next_trading}")
                target_date = next_trading
    
    # Attempt download with fallback chain
    download_strategy = [
        target_date,
        target_date - timedelta(days=1),
        target_date - timedelta(days=2),
        target_date - timedelta(days=3),
    ]
    
    for attempt_date in download_strategy:
        status = await _attempt_download(attempt_date)
        
        if status['success']:
            log.info(f"MCX margin download successful for {attempt_date}: {status['symbol_count']} symbols")
            await _log_download_attempt("MCX", attempt_date, "success", status)
            return status
        else:
            log.debug(f"MCX margin download failed for {attempt_date}")
    
    # All downloads failed; try database cache
    log.warning("All MCX margin downloads failed; falling back to database cache")
    loaded = await _load_latest_from_db()
    
    if loaded:
        log.info(f"Loaded {len(_mcx_store.margins)} MCX margins from database cache")
        await _log_download_attempt("MCX", target_date, "cached", {
            'symbol_count': len(_mcx_store.margins),
            'source': 'database_cache',
        })
        _mcx_store.fallback_used = True
        return {
            'success': True,
            'symbol_count': len(_mcx_store.margins),
            'source': 'database_cache',
            'download_date': _mcx_store.last_download,
        }
    
    # Complete failure: no fresh downloads AND no database cache
    # This only happens on first startup before any data downloaded
    log.error("MCX margin data completely unavailable: no downloads and no cache")
    log.error("MCX margin calculations will fail until data is downloaded")
    await _log_download_attempt("MCX", target_date, "failed", {
        'error': 'MCX margins unavailable - waiting for first successful download',
    })
    _mcx_store.fallback_used = False
    return {
        'success': False,
        'symbol_count': 0,
        'source': None,
        'download_date': None,
        'error': 'No MCX margin data available',
    }


async def _attempt_download(attempt_date: date_type) -> dict:
    """
    Attempt to download MCX SPAN Risk Parameter File for a specific date.
    
    MCX files are hosted at:
    https://www.mcxccl.com/risk-management/daily-span-risk-parameter-file/{YYYY}/{month}/mcxrpf-{YYYYMMDD}-{HHMM}-{SEQUENCE}-i.zip
    
    We download the Begin Day file (SEQUENCE=01, released around 05:06 IST).
    Files are updated daily and several times intra-day (09:30, 11:00, 12:00 IST).
    
    Returns dict with 'success' bool and optional 'symbol_count', 'file_size', 'error'
    """
    try:
        # Build month name for URL (e.g., "february" for Feb)
        month_names = [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december"
        ]
        month_name = month_names[attempt_date.month - 1]
        
        # MCX Begin Day file is typically YYYYMMDD-0506-01 (05:06 IST, sequence 01)
        date_str = attempt_date.strftime("%Y%m%d")
        year = attempt_date.strftime("%Y")
        
        # Try the Begin Day file URL (05:06 IST release, sequence 01)
        url = f"{_MCX_RPF_BASE}/{year}/{month_name}/mcxrpf-{date_str}-0506-01-i.zip"
        
        log.info(f"Downloading MCX SPAN file: {url}")
        
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            resp = await client.get(url)
            
            if resp.status_code == 404:
                # Try alternate times if 05:06 not available yet
                log.debug(f"Begin Day file (05:06) not found; trying intra-day files...")
                
                # Try 09:30 IST (sequence 02)
                url = f"{_MCX_RPF_BASE}/{year}/{month_name}/mcxrpf-{date_str}-0930-02-i.zip"
                log.debug(f"Trying: {url}")
                resp = await client.get(url)
                
                if resp.status_code == 404:
                    # Try 11:00 IST (sequence 03)
                    url = f"{_MCX_RPF_BASE}/{year}/{month_name}/mcxrpf-{date_str}-1100-03-i.zip"
                    log.debug(f"Trying: {url}")
                    resp = await client.get(url)
                
                if resp.status_code == 404:
                    # Try 12:00 IST (sequence 04)
                    url = f"{_MCX_RPF_BASE}/{year}/{month_name}/mcxrpf-{date_str}-1200-04-i.zip"
                    log.debug(f"Trying: {url}")
                    resp = await client.get(url)
            
            if resp.status_code != 200:
                log.warning(f"MCX SPAN file not available for {attempt_date}: HTTP {resp.status_code}")
                return {'success': False, 'error': f'HTTP {resp.status_code}', 'url': url}
            
            # Successfully downloaded ZIP file
            log.info(f"MCX SPAN file downloaded: {len(resp.content):,} bytes")
            
            # Parse ZIP contents and extract SPAN data
            parsed_margins = _parse_mcx_span_zip(resp.content)
            
            if not parsed_margins:
                log.error(f"MCX SPAN file parsing failed for {attempt_date}")
                return {'success': False, 'error': 'Failed to parse SPAN data from ZIP'}
            
            # Update in-memory store
            _mcx_store.margins.clear()
            _mcx_store.margins.update(parsed_margins)
            _mcx_store.last_download = attempt_date
            _mcx_store.fallback_used = False
            
            # Persist to database
            await _save_to_db(parsed_margins, attempt_date)
            
            log.info(f"MCX margin parsing successful: {len(parsed_margins)} symbols loaded")
            
            return {
                'success': True,
                'symbol_count': len(parsed_margins),
                'file_size': len(resp.content),
                'url': url,
            }
    
    except Exception as exc:
        log.warning(f"MCX SPAN download attempt failed for {attempt_date}: {exc}")
        return {'success': False, 'error': str(exc)}


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_mcx_span_zip(zip_content: bytes) -> dict[str, MCXMarginEntry]:
    """
    Parse MCX SPAN Risk Parameter ZIP file and extract margin data.
    
    MCX ZIP contains .spn XML files with SPAN margin parameters.
    Format is similar to NSE SPAN XML (SPAN 4.00 standard).
    
    Returns: {symbol → MCXMarginEntry}
    """
    try:
        with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
            names = zf.namelist()
            if not names:
                log.error("MCX SPAN ZIP is empty")
                return {}
            
            # Find .spn file (SPAN XML file)
            spn_name = next((n for n in names if n.endswith(".spn")), None)
            if not spn_name:
                log.error(f"No .spn file found in MCX ZIP (files: {names})")
                return {}
            
            log.info(f"MCX SPAN: extracting {spn_name}")
            with zf.open(spn_name) as f:
                xml_content = f.read()
            
            # Parse the XML content
            return _parse_mcx_span_xml(xml_content)
    
    except zipfile.BadZipFile as exc:
        log.error(f"MCX SPAN ZIP is corrupted: {exc}")
        return {}
    except Exception as exc:
        log.error(f"MCX SPAN ZIP extraction failed: {exc}")
        return {}


def _parse_mcx_span_xml(content_bytes: bytes) -> dict[str, MCXMarginEntry]:
    """
    Parse MCX SPAN XML file and extract per-commodity margin parameters.
    
    MCX SPAN XML format (SPAN 4.00):
      <phyPf>
        <pfCode>GOLD</pfCode>        ← commodity symbol
        <cvf>100</cvf>               ← contract value factor
        <phy>
          <p>62450.00</p>            ← reference price
          <scanRate>
            <r>1</r>                 ← rate 1 = initial margin
            <priceScan>3500.00</priceScan>  ← SPAN scan range
          </scanRate>
        </phy>
      </phyPf>
    
    Returns: {symbol → MCXMarginEntry}
    """
    result: dict[str, MCXMarginEntry] = {}
    
    try:
        root = ET.fromstring(content_bytes.decode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        log.warning(f"MCX SPAN XML parse failed ({exc}); trying regex fallback")
        return _parse_mcx_span_xml_regex(
            content_bytes.decode("utf-8", errors="replace")
        )
    
    count = 0
    for pf in root.iter("phyPf"):
        pf_code = (pf.findtext("pfCode") or "").strip().upper()
        if not pf_code:
            continue
        
        # CVF (contract value factor) at portfolio level
        try:
            cvf = float(pf.findtext("cvf") or 1.0)
        except ValueError:
            cvf = 1.0
        
        # Check hardcoded CVF table for overrides
        if pf_code in _MCX_CVF:
            cvf = _MCX_CVF[pf_code]
        
        # Find <phy> block with scanRate r=1 (initial margin)
        ref_price = 0.0
        price_scan = 0.0
        
        for phy in pf.findall("phy"):
            try:
                ref_price = float(phy.findtext("p") or 0)
            except ValueError:
                ref_price = 0.0
            
            # Check for CVF override inside phy
            try:
                phy_cvf = float(phy.findtext("cvf") or cvf)
            except ValueError:
                phy_cvf = cvf
            
            # Find rate-1 scanRate (initial margin)
            for sr in phy.findall("scanRate"):
                rate_id = (sr.findtext("r") or "").strip()
                if rate_id != "1":
                    continue
                
                try:
                    price_scan = float(sr.findtext("priceScan") or 0)
                except ValueError:
                    price_scan = 0.0
                
                if price_scan > 0:
                    # Default ELM for MCX commodities
                    # MCX typically uses 3-5% exposure margin
                    # Using 3% as conservative default
                    elm_pct = 3.0
                    
                    result[pf_code] = MCXMarginEntry(
                        symbol=pf_code,
                        ref_price=ref_price,
                        price_scan=price_scan,
                        cvf=phy_cvf if phy_cvf > 0 else cvf,
                        elm_pct=elm_pct,
                    )
                    count += 1
                    break
            else:
                continue
            break
    
    log.info(f"MCX SPAN XML parsed: {count} commodities")
    return result


def _parse_mcx_span_xml_regex(content: str) -> dict[str, MCXMarginEntry]:
    """
    Fallback MCX SPAN parser using regex for large files.
    
    Used when ElementTree cannot parse the full XML at once.
    """
    result: dict[str, MCXMarginEntry] = {}
    count = 0
    
    # Match each <phyPf>...</phyPf> block
    pf_pattern = re.compile(r"<phyPf>(.*?)</phyPf>", re.DOTALL)
    
    def extract_float(tag: str, blob: str) -> float:
        """Extract float value from XML tag."""
        match = re.search(rf"<{tag}>([\d.\-]+)</{tag}>", blob)
        return float(match.group(1)) if match else 0.0
    
    for pf_match in pf_pattern.finditer(content):
        blob = pf_match.group(1)
        
        # Extract symbol
        pf_code_match = re.search(r"<pfCode>([^<]+)</pfCode>", blob)
        if not pf_code_match:
            continue
        symbol = pf_code_match.group(1).strip().upper()
        
        # Extract CVF
        cvf_val = extract_float("cvf", blob)
        cvf = cvf_val if cvf_val > 0 else 1.0
        
        # Check hardcoded CVF
        if symbol in _MCX_CVF:
            cvf = _MCX_CVF[symbol]
        
        # Find <phy> block
        phy_match = re.search(r"<phy>(.*?)</phy>", blob, re.DOTALL)
        if not phy_match:
            continue
        
        phy_blob = phy_match.group(1)
        ref_price = extract_float("p", phy_blob)
        
        # Find rate-1 <scanRate>
        for sr_match in re.finditer(r"<scanRate>(.*?)</scanRate>", phy_blob, re.DOTALL):
            sr_blob = sr_match.group(1)
            if "<r>1</r>" not in sr_blob:
                continue
            
            price_scan = extract_float("priceScan", sr_blob)
            if price_scan > 0:
                elm_pct = 3.0  # Default 3% ELM for MCX
                
                result[symbol] = MCXMarginEntry(
                    symbol=symbol,
                    ref_price=ref_price,
                    price_scan=price_scan,
                    cvf=cvf,
                    elm_pct=elm_pct,
                )
                count += 1
            break
    
    log.info(f"MCX SPAN XML (regex) parsed: {count} commodities")
    return result


async def _load_latest_from_db() -> bool:
    """
    Load the most recent MCX SPAN data from database into memory cache.
    
    Returns True if data was loaded successfully.
    """
    if not get_pool:
        log.warning("Database not available; cannot load cached MCX margins")
        return False
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT symbol, ref_price, price_scan, contract_value_factor, elm_pct, downloaded_at
                FROM mcx_span_margin_cache
                WHERE is_latest = true
                ORDER BY symbol
                """
            )
            
            if not rows:
                log.info("No cached MCX margins found in database")
                return False
            
            _mcx_store.margins.clear()
            for row in rows:
                _mcx_store.margins[row['symbol']] = MCXMarginEntry(
                    symbol=row['symbol'],
                    ref_price=float(row['ref_price']),
                    price_scan=float(row['price_scan']),
                    cvf=float(row['contract_value_factor']),
                    elm_pct=float(row['elm_pct']),
                )
            
            if rows:
                _mcx_store.last_download = rows[0]['downloaded_at']
            
            log.info(f"Loaded {len(_mcx_store.margins)} MCX margins from database")
            return True
    
    except Exception as exc:
        log.error(f"Failed to load MCX margins from database: {exc}")
        return False


async def _save_to_db(
    margins: dict[str, MCXMarginEntry],
    download_date: date_type,
) -> None:
    """Save MCX margins to database for persistence."""
    if not get_pool:
        log.warning("Database not available; skipping MCX margin persistence")
        return
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = [
                (
                    entry.symbol,
                    entry.ref_price,
                    entry.price_scan,
                    entry.cvf,
                    entry.elm_pct,
                    download_date,
                )
                for entry in margins.values()
            ]
            
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO mcx_span_margin_cache 
                        (symbol, ref_price, price_scan, contract_value_factor, elm_pct, downloaded_at, is_latest)
                    VALUES ($1, $2, $3, $4, $5, $6, true)
                    """,
                    rows,
                )
                
                # Mark old entries as non-latest
                await conn.execute(
                    "UPDATE mcx_span_margin_cache SET is_latest = false WHERE downloaded_at < $1",
                    download_date,
                )
                
                log.info(f"Saved {len(rows)} MCX margins to database for {download_date}")
    
    except Exception as exc:
        log.error(f"Failed to save MCX margins to database: {exc}")


async def _log_download_attempt(
    exchange: str,
    download_date: date_type,
    status: str,
    details: dict,
) -> None:
    """Log MCX margin download attempt to database."""
    if not get_pool:
        return
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO margin_download_logs 
                    (exchange, download_date, status, symbol_count, error_message, file_sources)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (exchange, download_date) DO UPDATE
                SET status = EXCLUDED.status, symbol_count = EXCLUDED.symbol_count
                """,
                "MCX",
                download_date,
                status,
                details.get('symbol_count'),
                details.get('error'),
                details,
            )
    except Exception as exc:
        log.debug(f"Failed to log MCX download: {exc}")


async def get_margin(
    symbol: str,
    quantity: int,
    is_sell: bool = False,
) -> dict:
    """
    Calculate MCX margin requirement for a commodity position.
    
    Args:
        symbol: MCX commodity symbol (e.g., 'GOLD', 'SILVER')
        quantity: Number of contracts (lots)
        is_sell: True for seller (futures/forwards), False for buyer (options premium)
    
    Returns:
        dict with 'span_margin', 'elm_margin', 'total_margin' in INR
    """
    if symbol not in _mcx_store.margins:
        # ERROR: Symbol not in cache - cannot calculate margin without proper data
        log.error(f"MCX symbol {symbol} not found in margins cache; cannot provide margin calculation")
        return {
            'symbol': symbol,
            'span_margin': None,
            'elm_margin': None,
            'total_margin': None,
            'error': f'Symbol {symbol} not found in MCX margins cache',
        }
    
    entry = _mcx_store.margins[symbol]
    cvf = _MCX_CVF.get(symbol, entry.cvf)
    
    # SPAN margin = price_scan × quantity / cvf
    span_margin = entry.price_scan * quantity / cvf if cvf > 0 else 0
    
    # ELM margin = ref_price × quantity × elm_pct / 100 / cvf
    elm_margin = (entry.ref_price * quantity * entry.elm_pct / 100) / cvf if cvf > 0 else 0
    
    total_margin = span_margin + elm_margin if is_sell else 0
    
    return {
        'symbol': symbol,
        'ref_price': entry.ref_price,
        'price_scan': entry.price_scan,
        'cvf': cvf,
        'elm_pct': entry.elm_pct,
        'span_margin': round(span_margin, 2),
        'elm_margin': round(elm_margin, 2),
        'total_margin': round(total_margin, 2),
        'quantity': quantity,
        'is_sell': is_sell,
    }


def get_all_margins() -> dict[str, MCXMarginEntry]:
    """Return all cached MCX margins."""
    return _mcx_store.margins.copy()


def is_fallback_active() -> bool:
    """Check if fallback 3% ELM is currently in use."""
    return _mcx_store.fallback_used


# ── Daily Scheduler ───────────────────────────────────────────────────────────

class McxMarginScheduler:
    """
    Triggers download_and_refresh() at 08:45 IST each trading day.
    Runs alongside the NSE margin scheduler so both exchange margins are
    refreshed before markets open.

    If the download fails (holiday / early-morning unavailability), it retries
    every 15 minutes for up to 2 hours, then waits until the next day.
    """

    REFRESH_HOUR   = 8
    REFRESH_MINUTE = 45
    RETRY_INTERVAL = 15 * 60   # 15 minutes in seconds
    MAX_RETRIES    = 8          # up to 2 hours of retries

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self.last_run_at: Optional[datetime] = None
        self.last_run_error: Optional[str] = None
        self.last_run_success: Optional[bool] = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            log.warning("McxMarginScheduler already running")
            return
        self._task = asyncio.create_task(self._loop(), name="mcx_margin_scheduler")
        log.info(
            f"McxMarginScheduler started — refreshes at "
            f"{self.REFRESH_HOUR:02d}:{self.REFRESH_MINUTE:02d} IST daily."
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("McxMarginScheduler stopped.")

    async def _loop(self) -> None:
        while True:
            try:
                wait = self._seconds_until_next_refresh()
                log.info(
                    f"McxMarginScheduler: next refresh in "
                    f"{int(wait // 3600)}h {int((wait % 3600) // 60)}m."
                )
                await asyncio.sleep(wait)

                for attempt in range(1, self.MAX_RETRIES + 1):
                    log.info(
                        f"McxMarginScheduler: refresh attempt {attempt}/{self.MAX_RETRIES} …"
                    )
                    result = await download_and_refresh()
                    ok = bool(result.get("success"))
                    if ok:
                        self.last_run_at = datetime.now(IST)
                        self.last_run_success = True
                        self.last_run_error = None
                        break
                    if attempt < self.MAX_RETRIES:
                        log.warning(
                            f"MCX margin refresh attempt {attempt} failed; "
                            f"retrying in {self.RETRY_INTERVAL // 60}m …"
                        )
                        await asyncio.sleep(self.RETRY_INTERVAL)
                else:
                    self.last_run_at = datetime.now(IST)
                    self.last_run_success = False
                    self.last_run_error = "All retry attempts exhausted"
                    log.error(
                        "All MCX margin refresh attempts failed today; "
                        "carrying forward previous data until tomorrow."
                    )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_run_at = datetime.now(IST)
                self.last_run_success = False
                self.last_run_error = str(exc)
                log.exception(f"McxMarginScheduler error: {exc}. Retrying in 1 hour.")
                await asyncio.sleep(3600)

    def _seconds_until_next_refresh(self) -> float:
        now = datetime.now(IST)
        next_refresh = now.replace(
            hour=self.REFRESH_HOUR,
            minute=self.REFRESH_MINUTE,
            second=0,
            microsecond=0,
        )
        if now >= next_refresh:
            next_refresh = next_refresh + timedelta(days=1)
        return (next_refresh - now).total_seconds()


mcx_margin_scheduler = McxMarginScheduler()
