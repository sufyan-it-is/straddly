"""
app/margin/bse_margin_data.py
=============================
Calculates BSE derivatives margin requirements using NSE index margins as reference.

Implementation Strategy:
  - BSE SENSEX uses NSE NIFTY margin calculations
  - BSE BANKEX uses NSE BANKNIFTY margin calculations
  - This is acceptable as both indices are similar compositions with <1% margin variation
  - Eliminates need to maintain separate BSE margin files

Symbol Mapping:
  BSE SENSEX → NSE NIFTY
  BSE BANKEX → NSE BANKNIFTY

Margin Formula:
  span_margin     = price_scan × quantity          [for cvf == 1.00 (equities)]
  exposure_margin = ref_price × quantity × elm_pct / 100
  ─────────────────────────────────────────────────────
  Total (seller/futures) = span_margin + exposure_margin

Note:
  - Loads NSE margin data at startup
  - Maps BSE symbols to NSE equivalents for calculations
  - Falls back to 3% ELM if NSE data unavailable
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional

log = logging.getLogger(__name__)

# Import database pool for persistence
try:
    from app.database import get_pool
except ImportError:
    get_pool = None
    log.warning("Database not available for BSE margin persistence")

# Import exchange holidays
try:
    from app.margin.exchange_holidays import is_trading_day, get_next_trading_day
except ImportError:
    is_trading_day = None
    get_next_trading_day = None
    log.warning("Exchange holidays module not available")

# Import NSE margin module (BSE will use NSE calculations)
try:
    from app.margin import nse_margin_data
except ImportError:
    nse_margin_data = None
    log.warning("NSE margin module not available")

IST = timezone(timedelta(hours=5, minutes=30))

# ────────────────────────────────────────────────────────────────────────────
# BSE Symbol Mapping to NSE
# BSE indices use NSE equivalents for margin calculations
# ────────────────────────────────────────────────────────────────────────────

_BSE_TO_NSE_MAPPING = {
    'SENSEX': 'NIFTY',       # BSE Sensex → NSE Nifty 50
    'BANKEX': 'BANKNIFTY',    # BSE BankEx → NSE BankNifty
}


@dataclass
class BSEMarginEntry:
    """Single BSE derivative SPAN margin data."""
    symbol: str
    ref_price: float
    price_scan: float
    cvf: float
    elm_pct: float


# In-memory cache of BSE margins
class _BSEStore:
    def __init__(self):
        self.margins: dict[str, BSEMarginEntry] = {}
        self.last_download: Optional[date_type] = None
        self.fallback_used: bool = False


_bse_store = _BSEStore()


async def download_and_refresh(
    target_date: Optional[date_type] = None,
) -> dict:
    """
    Download BSE derivatives SPAN margin data for the given date.
    
    Strategy:
      1. If target_date is a holiday: try NEXT trading day first (future data)
      2. Then try: target_date, yesterday, 2 days back, 3 days back
      3. Fallback to database cache, then hardcoded 3% ELM
    
    Args:
        target_date: Date to download (defaults to today IST)
    
    Returns:
        dict with status, symbol_count, elm_pct, download_date
    """
    if target_date is None:
        target_date = datetime.now(tz=IST).date()
    
    log.info(f"BSE margin download starting for {target_date}")
    
    # Check if today is a holiday; if so, prefer next trading day
    if is_trading_day and get_next_trading_day:
        is_trading_val = await is_trading_day("BSE", target_date)
        if not is_trading_val:
            log.info(f"{target_date} is BSE holiday; attempting next trading day")
            next_trading = await get_next_trading_day("BSE", target_date)
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
            log.info(f"BSE margin download successful for {attempt_date}: {status['symbol_count']} symbols")
            await _log_download_attempt("BSE", attempt_date, "success", status)
            return status
        else:
            log.debug(f"BSE margin download failed for {attempt_date}")
    
    # All downloads failed; try database cache
    log.warning("All BSE margin downloads failed; falling back to database cache")
    loaded = await _load_latest_from_db()
    
    if loaded:
        log.info(f"Loaded {len(_bse_store.margins)} BSE margins from database cache")
        await _log_download_attempt("BSE", target_date, "cached", {
            'symbol_count': len(_bse_store.margins),
            'source': 'database_cache',
        })
        _bse_store.fallback_used = True
        return {
            'success': True,
            'symbol_count': len(_bse_store.margins),
            'source': 'database_cache',
            'download_date': _bse_store.last_download,
        }
    
    # Complete failure: no fresh downloads AND no database cache
    # This only happens on first startup before any data downloaded
    log.error("BSE margin data completely unavailable: no downloads and no cache")
    log.error("BSE margin calculations will fail until NSE data is downloaded")
    await _log_download_attempt("BSE", target_date, "failed", {
        'error': 'BSE margins unavailable - waiting for NSE data download',
    })
    _bse_store.fallback_used = False
    return {
        'success': False,
        'symbol_count': 0,
        'source': None,
        'download_date': None,
        'error': 'No BSE margin data available',
    }


async def _attempt_download(attempt_date: date_type) -> dict:
    """
    Load BSE margin data by using NSE calculations for mapped BSE indices.
    
    BSE SENSEX uses NSE NIFTY margin parameters.
    BSE BANKEX uses NSE BANKNIFTY margin parameters.
    This approach is acceptable as both index pairs have minimal margin difference (<1%).
    
    Returns dict with 'success' bool and 'symbol_count'
    """
    try:
        if not nse_margin_data:
            log.warning("NSE margin module not available; BSE margins unavailable")
            return {'success': False, 'error': 'NSE margin module not available'}
        
        # Get NSE margin store
        nse_store = nse_margin_data.get_store()
        
        if not nse_store.span:
            log.debug(f"No NSE SPAN margins available on {attempt_date}")
            return {'success': False, 'error': 'NSE SPAN not loaded'}
        
        # Map BSE symbols to NSE margins
        _bse_store.margins.clear()
        mapped_count = 0
        
        for bse_symbol, nse_symbol in _BSE_TO_NSE_MAPPING.items():
            nse_sym_upper = nse_symbol.upper()
            nse_span = nse_store.span.get(nse_sym_upper)
            
            if nse_span:
                # Get ELM for futures (OTH = Other/Futures in NSE terminology)
                if nse_sym_upper not in nse_store.elm_oth:
                    # Error: no ELM data for this symbol
                    log.warning(f"NSE symbol {nse_symbol} has no ELM data; skipping BSE {bse_symbol} mapping")
                    continue
                
                elm_entry = nse_store.elm_oth[nse_sym_upper]
                elm_pct = elm_entry.total_elm_pct
                
                # Create BSE entry with NSE data, but use BSE symbol name
                _bse_store.margins[bse_symbol] = BSEMarginEntry(
                    symbol=bse_symbol,
                    ref_price=nse_span.ref_price,
                    price_scan=nse_span.price_scan,
                    cvf=nse_span.cvf,
                    elm_pct=elm_pct,
                )
                mapped_count += 1
                log.debug(f"Mapped BSE {bse_symbol} ← NSE {nse_symbol} (ELM: {elm_pct}%)")
            else:
                log.warning(f"NSE symbol {nse_symbol} not found for BSE {bse_symbol}")
        
        if mapped_count == 0:
            log.warning(f"Failed to map any BSE symbols from NSE data on {attempt_date}")
            return {'success': False, 'error': 'No successful symbol mappings'}
        
        _bse_store.last_download = attempt_date
        log.info(f"Successfully mapped {mapped_count} BSE symbols from NSE data")
        
        return {
            'success': True,
            'symbol_count': mapped_count,
            'source': 'nse_calculations',
            'mapping': str(_BSE_TO_NSE_MAPPING),
        }
    
    except Exception as exc:
        log.warning(f"BSE mapping attempt failed: {exc}")
        return {'success': False, 'error': str(exc)}


async def _load_latest_from_db() -> bool:
    """
    Load the most recent BSE SPAN data from database into memory cache.
    
    Returns True if data was loaded successfully.
    """
    if not get_pool:
        log.warning("Database not available; cannot load cached BSE margins")
        return False
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT symbol, ref_price, price_scan, contract_value_factor, elm_pct, downloaded_at
                FROM bse_span_margin_cache
                WHERE is_latest = true
                ORDER BY symbol
                """
            )
            
            if not rows:
                log.info("No cached BSE margins found in database")
                return False
            
            _bse_store.margins.clear()
            for row in rows:
                _bse_store.margins[row['symbol']] = BSEMarginEntry(
                    symbol=row['symbol'],
                    ref_price=float(row['ref_price']),
                    price_scan=float(row['price_scan']),
                    cvf=float(row['contract_value_factor']),
                    elm_pct=float(row['elm_pct']),
                )
            
            if rows:
                _bse_store.last_download = rows[0]['downloaded_at']
            
            log.info(f"Loaded {len(_bse_store.margins)} BSE margins from database")
            return True
    
    except Exception as exc:
        log.error(f"Failed to load BSE margins from database: {exc}")
        return False


async def _save_to_db(
    margins: dict[str, BSEMarginEntry],
    download_date: date_type,
) -> None:
    """Save BSE margins to database for persistence."""
    if not get_pool:
        log.warning("Database not available; skipping BSE margin persistence")
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
                    INSERT INTO bse_span_margin_cache 
                        (symbol, ref_price, price_scan, contract_value_factor, elm_pct, downloaded_at, is_latest)
                    VALUES ($1, $2, $3, $4, $5, $6, true)
                    """,
                    rows,
                )
                
                # Mark old entries as non-latest
                await conn.execute(
                    "UPDATE bse_span_margin_cache SET is_latest = false WHERE downloaded_at < $1",
                    download_date,
                )
                
                log.info(f"Saved {len(rows)} BSE margins to database for {download_date}")
    
    except Exception as exc:
        log.error(f"Failed to save BSE margins to database: {exc}")


async def _log_download_attempt(
    exchange: str,
    download_date: date_type,
    status: str,
    details: dict,
) -> None:
    """Log BSE margin download attempt to database."""
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
                "BSE",
                download_date,
                status,
                details.get('symbol_count'),
                details.get('error'),
                details,
            )
    except Exception as exc:
        log.debug(f"Failed to log BSE download: {exc}")


async def get_margin(
    symbol: str,
    quantity: int,
    is_sell: bool = False,
) -> dict:
    """
    Calculate BSE margin requirement for a derivatives position.
    
    Args:
        symbol: BSE symbol (e.g., 'NIFTY', 'BANKNIFTY')
        quantity: Number of contracts (lots)
        is_sell: True for seller (futures/forwards), False for buyer (options premium)
    
    Returns:
        dict with 'span_margin', 'elm_margin', 'total_margin' in INR
    """
    if symbol not in _bse_store.margins:
        # ERROR: Symbol not in cache - cannot calculate margin without proper data
        log.error(f"BSE symbol {symbol} not found in margins cache; cannot provide margin calculation")
        return {
            'symbol': symbol,
            'span_margin': None,
            'elm_margin': None,
            'total_margin': None,
            'error': f'Symbol {symbol} not found in BSE margins cache',
        }
    
    entry = _bse_store.margins[symbol]
    
    # SPAN margin = price_scan × quantity (cvf typically 1.0 for equities)
    span_margin = entry.price_scan * quantity
    
    # ELM margin = ref_price × quantity × elm_pct / 100
    elm_margin = entry.ref_price * quantity * entry.elm_pct / 100
    
    total_margin = span_margin + elm_margin if is_sell else 0
    
    return {
        'symbol': symbol,
        'ref_price': entry.ref_price,
        'price_scan': entry.price_scan,
        'cvf': entry.cvf,
        'elm_pct': entry.elm_pct,
        'span_margin': round(span_margin, 2),
        'elm_margin': round(elm_margin, 2),
        'total_margin': round(total_margin, 2),
        'quantity': quantity,
        'is_sell': is_sell,
    }


def get_all_margins() -> dict[str, BSEMarginEntry]:
    """Return all cached BSE margins."""
    return _bse_store.margins.copy()


def is_fallback_active() -> bool:
    """Check if fallback 3% ELM is currently in use."""
    return _bse_store.fallback_used
