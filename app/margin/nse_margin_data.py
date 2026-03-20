"""
app/margin/nse_margin_data.py
=================================
Downloads and parses 3 NSE margin files daily, serving real SPAN + ELM
margin data for all margin calculations.

Files downloaded from NSE Archives:
  1. F&O Exposure Limit CSV  (ael)
       https://nsearchives.nseindia.com/archives/exp_lim/ael_{DDMMYYYY}.csv
  2. F&O Begin Day SPAN® XML  (nsccl)
       https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.{YYYYMMDD}.i1.zip
  3. COM Begin Day SPAN® XML  (nsccl_o)
       https://nsearchives.nseindia.com/archives/com/span/nsccl_o.{YYYYMMDD}.i1.zip

SPAN XML format (SPAN 4.00):
  <phyPf>
    <pfCode>NIFTY</pfCode>     ← underlying symbol (uppercase)
    <cvf>1.00</cvf>            ← contract value factor (1.0 for equity, >1 for commodity)
    <phy>
      <p>25454.35</p>          ← underlying reference price
      <scanRate>
        <r>1</r>               ← rate 1 = initial margin rate
        <priceScan>2367.25</priceScan>   ← SPAN scan range (INR per underlying unit)
      </scanRate>
    </phy>
  </phyPf>

Margin Formula:
  span_margin     = priceScan × quantity          [for cvf == 1.00 (equities)]
  span_margin     = priceScan × quantity / cvf    [for cvf > 1.00 (commodities)]
  exposure_margin = ref_price × quantity × elm_pct / 100
  ─────────────────────────────────────────────────────
  Total (seller / futures) = span_margin + exposure_margin
  Total (option buyer)     = ltp × quantity  (premium only)

ELM CSV format:
  Sr No.,Symbol,Instrument Type,Normal ELM%,Additional ELM%,Total ELM%
  Instrument Type: OTH = Futures/Others, OTM = Out-of-the-money Options

Notes:
  - NIFTY/BANKNIFTY/index underlyings are NOT in the ELM file.
    A 3% default exposure limit applies for index derivatives (SEBI mandate).
  - NSE requires an initialising browser-like session (cookie) to access
    nsearchives.nseindia.com.  The downloader visits the NSE main page first
    to obtain the required session cookies before fetching the data files.
  - Files are fetched once per day at 08:45 IST (before NSE opens at 09:15).
    Previous-day data is retained if the current day's files are unavailable
    (weekends, holidays, network failure).
"""

import asyncio
import csv
import io
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional
import xml.etree.ElementTree as ET

import httpx

log = logging.getLogger(__name__)

# Import database pool for persistence
try:
    from app.database import get_pool
except ImportError:
    get_pool = None
    log.warning("Database not available for SPAN margin persistence")

from app.runtime.notifications import add_notification

# Import holiday management for multi-exchange support
try:
    from app.margin.exchange_holidays import (
        is_trading_day,
        get_next_trading_day,
    )
except ImportError:
    is_trading_day = None
    get_next_trading_day = None
    log.warning("Exchange holidays module not available; using fallback")

# Fallback: old is_holiday function from market_hours
try:
    from app.market_hours import _is_holiday
except ImportError:
    def _is_holiday(d: date_type) -> bool:
        return d.weekday() >= 5  # Weekend only

IST = timezone(timedelta(hours=5, minutes=30))

# ── NSE Archive URL Templates ─────────────────────────────────────────────────

# Base session URL (visited first to obtain cookies)
_NSE_HOME   = "https://www.nseindia.com/"
_NSE_DERIV  = "https://www.nseindia.com/all-reports-derivatives"

# F&O Exposure Limit   — date format: DDMMYYYY (e.g. 20022026)
_AEL_URL    = "https://nsearchives.nseindia.com/archives/exp_lim/ael_{DDMMYYYY}.csv"

# F&O Begin Day SPAN   — date format: YYYYMMDD (e.g. 20260220)
_FO_SPAN_URL  = "https://nsearchives.nseindia.com/archives/nsccl/span/nsccl.{YYYYMMDD}.i1.zip"

# COM Begin Day SPAN   — date format: YYYYMMDD
_COM_SPAN_URL = "https://nsearchives.nseindia.com/archives/com/span/nsccl_o.{YYYYMMDD}.i1.zip"


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class SpanEntry:
    """SPAN margin parameters for one underlying symbol."""
    symbol:      str
    ref_price:   float   # underlying reference price (INR)
    price_scan:  float   # SPAN scan range (INR per underlying unit / per lot for cvf>1)
    cvf:         float   # contract value factor (1.0 for equity, >1 for commodity)
    source:      str     # "fo" | "com"


@dataclass
class ElmEntry:
    """Exposure Limit Margin rates for one symbol + instrument type."""
    symbol:          str
    instrument_type: str      # "OTH" (futures) | "OTM" (options)
    normal_elm_pct:  float
    additional_elm_pct: float
    total_elm_pct:   float


@dataclass
class MarginDataStore:
    """In-memory cache of parsed NSE margin data."""
    span:     dict[str, SpanEntry] = field(default_factory=dict)  # symbol → SpanEntry
    elm_oth:  dict[str, ElmEntry]  = field(default_factory=dict)  # symbol → ELM for futures
    elm_otm:  dict[str, ElmEntry]  = field(default_factory=dict)  # symbol → ELM for options
    as_of:    Optional[datetime]   = None  # when the data was last refreshed
    ready:    bool                 = False


# Singleton store
_store = MarginDataStore()


def get_store() -> MarginDataStore:
    return _store


# ── Public Lookup API ─────────────────────────────────────────────────────────

def get_span_data(symbol: str) -> Optional[SpanEntry]:
    """Return SPAN entry for a symbol (e.g. 'NIFTY', 'BANKNIFTY', 'RELIANCE')."""
    return _store.span.get(symbol.upper())


def get_elm_futures(symbol: str) -> Optional[float]:
    """Return Total ELM % for futures/others for a symbol. Returns None if not found."""
    e = _store.elm_oth.get(symbol.upper())
    return e.total_elm_pct if e else None


def get_elm_options(symbol: str) -> Optional[float]:
    """Return Total ELM % for OTM options for a symbol. Returns None if not found."""
    e = _store.elm_otm.get(symbol.upper())
    return e.total_elm_pct if e else None


# ── Database Persistence Functions ───────────────────────────────────────────

async def _save_span_to_db(span_data: dict[str, SpanEntry], download_date: date_type) -> None:
    """Save SPAN margin data to database for persistence."""
    if not get_pool:
        log.warning("Database not available; skipping SPAN persistence")
        return
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            # Insert all SPAN entries
            rows = [
                (
                    entry.symbol,
                    entry.ref_price,
                    entry.price_scan,
                    entry.cvf,
                    entry.source,
                    download_date,
                )
                for entry in span_data.values()
            ]
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO span_margin_cache 
                        (symbol, ref_price, price_scan, cvf, source, downloaded_at, is_latest)
                    VALUES ($1, $2, $3, $4, $5, $6, true)
                    """,
                    rows,
                )
                log.info(f"Saved {len(rows)} SPAN entries to database")
    except Exception as exc:
        log.error(f"Failed to save SPAN data to database: {exc}")


async def _save_elm_to_db(
    elm_oth: dict[str, ElmEntry],
    elm_otm: dict[str, ElmEntry],
    download_date: date_type,
) -> None:
    """Save ELM margin data to database for persistence."""
    if not get_pool:
        log.warning("Database not available; skipping ELM persistence")
        return
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            # Combine OTH and OTM entries
            rows = []
            for entry in elm_oth.values():
                rows.append(
                    (
                        entry.symbol,
                        entry.instrument_type,
                        entry.normal_elm_pct,
                        entry.additional_elm_pct,
                        entry.total_elm_pct,
                        download_date,
                    )
                )
            for entry in elm_otm.values():
                rows.append(
                    (
                        entry.symbol,
                        entry.instrument_type,
                        entry.normal_elm_pct,
                        entry.additional_elm_pct,
                        entry.total_elm_pct,
                        download_date,
                    )
                )
            
            if rows:
                await conn.executemany(
                    """
                    INSERT INTO elm_margin_cache 
                        (symbol, instrument_type, normal_elm_pct, additional_elm_pct, 
                         total_elm_pct, downloaded_at, is_latest)
                    VALUES ($1, $2, $3, $4, $5, $6, true)
                    """,
                    rows,
                )
                log.info(f"Saved {len(rows)} ELM entries to database")
    except Exception as exc:
        log.error(f"Failed to save ELM data to database: {exc}")


async def _load_latest_from_db() -> bool:
    """Load the most recent SPAN/ELM data from database into memory store."""
    if not get_pool:
        log.warning("Database not available; cannot load cached SPAN data")
        return False
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            # Load latest SPAN data
            span_rows = await conn.fetch(
                """
                SELECT symbol, ref_price, price_scan, cvf, source, downloaded_at
                FROM span_margin_cache
                WHERE is_latest = true
                ORDER BY symbol
                """
            )
            
            for row in span_rows:
                _store.span[row['symbol']] = SpanEntry(
                    symbol=row['symbol'],
                    ref_price=float(row['ref_price']),
                    price_scan=float(row['price_scan']),
                    cvf=float(row['cvf']),
                    source=row['source'],
                )
            
            # Load latest ELM data
            elm_rows = await conn.fetch(
                """
                SELECT symbol, instrument_type, normal_elm_pct, 
                       additional_elm_pct, total_elm_pct, downloaded_at
                FROM elm_margin_cache
                WHERE is_latest = true
                ORDER BY symbol, instrument_type
                """
            )
            
            for row in elm_rows:
                entry = ElmEntry(
                    symbol=row['symbol'],
                    instrument_type=row['instrument_type'],
                    normal_elm_pct=float(row['normal_elm_pct']),
                    additional_elm_pct=float(row['additional_elm_pct']),
                    total_elm_pct=float(row['total_elm_pct']),
                )
                if row['instrument_type'] == 'OTH':
                    _store.elm_oth[row['symbol']] = entry
                elif row['instrument_type'] == 'OTM':
                    _store.elm_otm[row['symbol']] = entry
            
            if span_rows or elm_rows:
                # Get the most recent download timestamp
                latest_ts = None
                if span_rows:
                    latest_ts = span_rows[0]['downloaded_at']
                elif elm_rows:
                    latest_ts = elm_rows[0]['downloaded_at']
                
                _store.as_of = latest_ts
                _store.ready = True
                
                log.info(
                    f"Loaded SPAN margin data from database: "
                    f"{len(_store.span)} SPAN symbols, "
                    f"{len(_store.elm_oth)} ELM-OTH, "
                    f"{len(_store.elm_otm)} ELM-OTM "
                    f"[cached from {latest_ts.strftime('%d-%b-%Y') if latest_ts else 'unknown'}]"
                )
                return True
            else:
                log.info("No cached SPAN data found in database")
                return False
    
    except Exception as exc:
        log.error(f"Failed to load SPAN data from database: {exc}")
        return False


async def _create_system_notification(
    category: str,
    severity: str,
    title: str,
    message: str,
    details: Optional[dict] = None,
) -> None:
    """Create a system notification for the admin dashboard."""
    if not get_pool:
        log.warning(f"Cannot create notification (no DB): {title}")
        return

    try:
        inserted = await add_notification(
            category=category,
            severity=severity,
            title=title,
            message=message,
            details=details,
            dedupe_key=f"{category}:{title}",
            dedupe_ttl_seconds=180,
        )
        if inserted:
            log.info(f"Created {severity} notification: {title}")
    except Exception as exc:
        log.error(f"Failed to create notification: {exc}")


async def _log_download_attempt(
    download_date: date_type,
    status: str,
    span_symbols: int,
    elm_futures: int,
    elm_options: int,
    error_message: Optional[str] = None,
    files_downloaded: Optional[dict] = None,
) -> None:
    """Log a SPAN download attempt to the database."""
    if not get_pool:
        return
    
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO span_download_log 
                    (download_date, status, span_symbols, elm_futures, elm_options, 
                     error_message, files_downloaded)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                download_date,
                status,
                span_symbols,
                elm_futures,
                elm_options,
                error_message,
                files_downloaded,
            )
    except Exception as exc:
        log.error(f"Failed to log download attempt: {exc}")


def calculate_margin(
    symbol:           str,
    transaction_type: str,   # "BUY" | "SELL"
    quantity:         int,   # in underlying units (lots × lot_size)
    ltp:              float, # last traded price (for premium / exposure base)
    is_option:        bool,
    is_futures:       bool,
    is_commodity:     bool = False,
) -> dict:
    """
    Compute margin using real NSE SPAN + ELM data.

    Returns a dict with breakdown:
      span_margin, exposure_margin, premium, total_margin
    """
    sym = symbol.upper()
    qty = max(1, int(quantity))

    span_entry = get_span_data(sym)

    # ── SPAN margin ───────────────────────────────────────────────────────────
    if span_entry and span_entry.price_scan > 0:
        cvf = span_entry.cvf or 1.0
        if cvf <= 1.0:
            # Equity derivatives: priceScan is per underlying unit
            span_margin = span_entry.price_scan * qty
        else:
            # Commodity: priceScan is per contract; scale by lots
            # (quantity / cvf = number of contracts/lots)
            lots = qty / cvf
            span_margin = span_entry.price_scan * lots
        ref_price = span_entry.ref_price or ltp
    else:
        # ERROR: No SPAN data available for symbol
        # Do not use hardcoded percentage fallbacks - they are inaccurate
        log.error(f"SPAN data unavailable for symbol {sym}; cannot calculate margin")
        return {
            "span_margin":     None,
            "exposure_margin": None,
            "premium":         None,
            "total_margin":    None,
            "elm_pct":         None,
            "error":           f"SPAN data not available for {sym}",
            "data_as_of":      _store.as_of.isoformat() if _store.as_of else None,
        }

    # ── Exposure margin ───────────────────────────────────────────────────────
    if is_option and transaction_type.upper() == "BUY":
        # Option buyers pay only the premium — no exposure margin separate
        elm_pct = 0.0
    elif is_option:
        elm_pct = get_elm_options(sym)
        if elm_pct is None:
            # NSE AEL (Exposure Limit) data is often not published for index
            # derivatives (e.g., NIFTY/BANKNIFTY). In that case, treat exposure
            # margin as 0% and still return SPAN so the UI can display a usable
            # required margin.
            if sym.upper() in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}:
                log.warning(
                    "ELM%%OTM data unavailable for index option %s; using 0%% exposure",
                    sym,
                )
                elm_pct = 0.0
            else:
                log.error(f"ELM%OTM data unavailable for option {sym}; cannot calculate margin")
                return {
                    "span_margin":     None,
                    "exposure_margin": None,
                    "premium":         None,
                    "total_margin":    None,
                    "elm_pct":         None,
                    "error":           f"ELM data not available for option {sym}",
                    "data_as_of":      _store.as_of.isoformat() if _store.as_of else None,
                }
    else:
        elm_pct = get_elm_futures(sym)
        if elm_pct is None:
            if sym.upper() in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}:
                log.warning(
                    "ELM%%OTH data unavailable for index futures %s; using 0%% exposure",
                    sym,
                )
                elm_pct = 0.0
            else:
                log.error(f"ELM%OTH data unavailable for futures {sym}; cannot calculate margin")
                return {
                    "span_margin":     None,
                    "exposure_margin": None,
                    "premium":         None,
                    "total_margin":    None,
                    "elm_pct":         None,
                    "error":           f"ELM data not available for futures {sym}",
                    "data_as_of":      _store.as_of.isoformat() if _store.as_of else None,
                }

    exposure_margin = ref_price * qty * (elm_pct / 100.0)

    # ── Total margin ──────────────────────────────────────────────────────────
    if is_option and transaction_type.upper() == "BUY":
        # Buyer: entire premium is the margin
        premium       = ltp * qty
        total_margin  = premium
        span_margin   = 0.0
        exposure_margin = 0.0
    else:
        # Seller / futures: SPAN + Exposure
        premium      = 0.0
        total_margin = span_margin + exposure_margin

    return {
        "span_margin":     round(span_margin, 2),
        "exposure_margin": round(exposure_margin, 2),
        "premium":         round(premium, 2),
        "total_margin":    round(total_margin, 2),
        "elm_pct":         round(elm_pct, 4),
        "span_source":     span_entry.source if span_entry else "fallback",
        "data_as_of":      _store.as_of.isoformat() if _store.as_of else None,
    }


# ── Downloader ────────────────────────────────────────────────────────────────

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection":      "keep-alive",
}


def _date_ddmmyyyy(dt: datetime) -> str:
    return dt.strftime("%d%m%Y")


def _date_yyyymmdd(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


async def _get_session_client() -> httpx.AsyncClient:
    """
    Create an httpx client that has visited the NSE main page to obtain
    the cookies required for accessing nsearchives.nseindia.com.
    """
    client = httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers=_BROWSER_HEADERS,
    )
    try:
        log.debug("NSE margin downloader: getting session cookies …")
        await client.get(_NSE_HOME,  timeout=20.0)
        await asyncio.sleep(1.5)
        await client.get(_NSE_DERIV, timeout=20.0)
        await asyncio.sleep(1.0)
    except Exception as exc:
        log.warning(f"NSE session setup partial failure (will retry downloads): {exc}")
    return client


async def _download_ael_csv(client: httpx.AsyncClient, dt: datetime) -> Optional[str]:
    """Download the F&O Exposure Limit CSV and return its text content."""
    url = _AEL_URL.replace("{DDMMYYYY}", _date_ddmmyyyy(dt))
    log.info(f"Downloading AEL: {url}")
    try:
        resp = await client.get(url, timeout=30.0)
        resp.raise_for_status()
        content = resp.text
        # Sanity check: should start with "Sr No."
        if "Sr No." not in content[:50]:
            log.warning(f"AEL response looks invalid (size={len(content)})")
            return None
        log.info(f"AEL downloaded: {len(content):,} chars, {content.count(chr(10))} rows")
        return content
    except Exception as exc:
        log.error(f"AEL download failed for {url}: {exc}")
        return None


async def _download_span_zip(
    client: httpx.AsyncClient, url: str, label: str
) -> Optional[bytes]:
    """Download a SPAN® .zip file and return the uncompressed .spn XML bytes."""
    log.info(f"Downloading {label} SPAN: {url}")
    try:
        resp = await client.get(url, timeout=120.0)
        resp.raise_for_status()
        raw = resp.content
        log.info(f"{label} SPAN zip: {len(raw):,} bytes compressed")
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            if not names:
                log.error(f"{label} SPAN zip is empty")
                return None
            spn_name = next((n for n in names if n.endswith(".spn")), names[0])
            log.info(f"{label} SPAN: extracting {spn_name}")
            with zf.open(spn_name) as f:
                return f.read()
    except Exception as exc:
        log.error(f"{label} SPAN download failed for {url}: {exc}")
        return None


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_ael_csv(content: str) -> dict[str, dict]:
    """
    Parse the AEL Exposure Limit CSV.

    CSV header (note: "Additional ELM%" column embeds the trade date):
      Sr No.,Symbol,Instrument Type,Normal ELM Margin%,
      Additional ELM% for Trade Date DD-Mon-YYYY,Total applicable ELM%

    Returns: {symbol → {"OTH": ElmEntry, "OTM": ElmEntry}}
    """
    result: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(content))

    # Map variable-name "Additional ELM% for Trade Date …" column dynamically
    fieldnames = reader.fieldnames or []
    add_elm_col = next(
        (f for f in fieldnames if "Additional" in f and "ELM" in f), None
    )
    total_elm_col = next(
        (f for f in fieldnames if "Total" in f and "ELM" in f), None
    )
    normal_elm_col = next(
        (f for f in fieldnames if "Normal" in f and "ELM" in f), None
    )

    count = 0
    for row in reader:
        sym   = (row.get("Symbol") or "").strip().upper()
        itype = (row.get("Instrument Type") or "").strip().upper()
        if not sym or itype not in ("OTH", "OTM"):
            continue
        try:
            normal_elm = float(row.get(normal_elm_col or "", 0) or 0)
            add_elm    = float(row.get(add_elm_col    or "", 0) or 0)
            total_elm  = float(row.get(total_elm_col  or "", 0) or normal_elm)
        except (ValueError, TypeError):
            continue

        entry = ElmEntry(
            symbol=sym,
            instrument_type=itype,
            normal_elm_pct=normal_elm,
            additional_elm_pct=add_elm,
            total_elm_pct=total_elm,
        )
        if sym not in result:
            result[sym] = {}
        result[sym][itype] = entry
        count += 1

    log.info(f"AEL parsed: {len(result)} symbols ({count} rows)")
    return result


def _parse_span_xml(content_bytes: bytes, source: str) -> dict[str, SpanEntry]:
    """
    Parse a SPAN® 4.00 XML file and extract per-symbol margin parameters.

    Returns: {symbol → SpanEntry}
    """
    result: dict[str, SpanEntry] = {}
    try:
        root = ET.fromstring(content_bytes.decode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        # The file may be very large; try finding the pointInTime section only
        log.warning(f"Full XML parse failed ({exc}); trying chunked extraction …")
        return _parse_span_xml_regex(
            content_bytes.decode("utf-8", errors="replace"), source
        )

    # Walk all <phyPf> elements
    count = 0
    for pf in root.iter("phyPf"):
        pf_code = (pf.findtext("pfCode") or "").strip().upper()
        if not pf_code:
            continue

        # CVF at the portfolio level
        try:
            cvf = float(pf.findtext("cvf") or 1.0)
        except ValueError:
            cvf = 1.0

        # Find the <phy> block with scanRate r=1 (initial margin)
        for phy in pf.findall("phy"):
            try:
                ref_price = float(phy.findtext("p") or 0)
            except ValueError:
                ref_price = 0.0

            # Get <cvf> override inside phy if present
            try:
                phy_cvf = float(phy.findtext("cvf") or cvf)
            except ValueError:
                phy_cvf = cvf

            for sr in phy.findall("scanRate"):
                rate_id = (sr.findtext("r") or "").strip()
                if rate_id != "1":
                    continue
                try:
                    price_scan = float(sr.findtext("priceScan") or 0)
                except ValueError:
                    price_scan = 0.0

                if price_scan > 0:
                    result[pf_code] = SpanEntry(
                        symbol=pf_code,
                        ref_price=ref_price,
                        price_scan=price_scan,
                        cvf=phy_cvf,
                        source=source,
                    )
                    count += 1
                    break
            else:
                continue
            break

    log.info(f"SPAN {source}: parsed {count} symbols from XML")
    return result


def _parse_span_xml_regex(content: str, source: str) -> dict[str, SpanEntry]:
    """
    Fallback SPAN parser using regex for large files where ElementTree
    is unable to parse the entire document at once.
    """
    import re
    result: dict[str, SpanEntry] = {}
    count = 0

    # Match each <phyPf>...</phyPf> block
    pf_pat  = re.compile(r"<phyPf>(.*?)</phyPf>", re.DOTALL)
    c_float = lambda tag, blob: float(m.group(1)) if (m := re.search(rf"<{tag}>([\d.\-]+)</{tag}>", blob)) else 0.0

    for m_pf in pf_pat.finditer(content):
        blob    = m_pf.group(1)
        pf_code = re.search(r"<pfCode>([^<]+)</pfCode>", blob)
        if not pf_code:
            continue
        sym = pf_code.group(1).strip().upper()

        cvf_val  = c_float("cvf", blob)
        cvf      = cvf_val if cvf_val > 0 else 1.0

        # Find <phy> block
        phy_m = re.search(r"<phy>(.*?)</phy>", blob, re.DOTALL)
        if not phy_m:
            continue
        phy_blob  = phy_m.group(1)
        ref_price = c_float("p", phy_blob)

        # Find rate-1 <scanRate>
        for m_sr in re.finditer(r"<scanRate>(.*?)</scanRate>", phy_blob, re.DOTALL):
            sr_blob = m_sr.group(1)
            if "<r>1</r>" not in sr_blob:
                continue
            price_scan = c_float("priceScan", sr_blob)
            if price_scan > 0:
                result[sym] = SpanEntry(
                    symbol=sym,
                    ref_price=ref_price,
                    price_scan=price_scan,
                    cvf=cvf,
                    source=source,
                )
                count += 1
            break

    log.info(f"SPAN {source} (regex): parsed {count} symbols")
    return result


# ── Helper Functions ──────────────────────────────────────────────────────────

def _is_holiday_fallback(d: date_type) -> bool:
    """
    Fallback holiday check if exchange_holidays module unavailable.
    Only checks for weekends.
    """
    return d.weekday() >= 5


# ── Main Refresh Function ─────────────────────────────────────────────────────

async def download_and_refresh(date: Optional[datetime] = None) -> bool:
    """
    Download all 3 NSE margin files for the given date (default: today IST)
    and update the in-memory store.

    Strategy:
      1. If today is a market holiday/weekend → try NEXT working day first
      2. If that fails OR today is a working day → fallback to previous days
      3. If all downloads fail → load from database cache
      4. If no cache → use 9% fallback margin

    Returns True on full success, False if any file failed (partial data
    still applied from the successful downloads).
    """
    dt = date or datetime.now(IST)
    original_date = dt.date()
    is_today_holiday = _is_holiday_fallback(original_date)
    
    # Check using database-backed holidays if available
    if is_trading_day:
        try:
            is_today_holiday = not await is_trading_day("NSE", original_date)
        except Exception as exc:
            log.warning(f"Failed to check holiday status: {exc}; using fallback")
    
    log.info(
        f"NSE margin refresh starting for {dt.strftime('%d-%b-%Y')} "
        f"({'holiday/weekend' if is_today_holiday else 'working day'}) …"
    )

    # ── STRATEGY 1: If today is a holiday, try NEXT working day first ────────
    if is_today_holiday:
        next_working = None
        if get_next_trading_day:
            try:
                next_working = await get_next_trading_day("NSE", original_date, max_days=7)
            except Exception as exc:
                log.warning(f"Could not get next trading day: {exc}")
        
        if next_working:
            next_dt = datetime.combine(next_working, dt.time()).replace(tzinfo=IST)
            log.info(
                f"Today is a holiday/weekend. Trying next working day: "
                f"{next_working.strftime('%d-%b-%Y')}..."
            )
            
            success, files_status = await _attempt_download(next_dt)
            
            if success:
                # Save to database
                try:
                    await _save_span_to_db(_store.span, original_date)
                    await _save_elm_to_db(_store.elm_oth, _store.elm_otm, original_date)
                    await _log_download_attempt(
                        original_date,
                        "future_working_day",
                        len(_store.span),
                        len(_store.elm_oth),
                        len(_store.elm_otm),
                        None,
                        files_status,
                    )
                    
                    # Notify admin about using future data
                    days_ahead = (next_working - original_date).days
                    await _create_system_notification(
                        category="span_download",
                        severity="info",
                        title=f"SPAN Data: Using next working day's data",
                        message=f"Today ({original_date.strftime('%d-%b-%Y')}) is a market holiday. "
                                f"Downloaded SPAN data for next working day: {next_working.strftime('%d-%b-%Y')}.",
                        details={
                            "requested_date": original_date.isoformat(),
                            "actual_date": next_working.isoformat(),
                            "days_ahead": days_ahead,
                            "span_symbols": len(_store.span),
                            "elm_futures": len(_store.elm_oth),
                            "elm_options": len(_store.elm_otm),
                        },
                    )
                except Exception as exc:
                    log.error(f"Failed to save SPAN data to database: {exc}")
                
                log.info(
                    f"✓ Successfully downloaded next working day data "
                    f"({next_working.strftime('%d-%b-%Y')})"
                )
                return True
            else:
                log.warning(
                    f"Next working day ({next_working.strftime('%d-%b-%Y')}) "
                    f"download failed. Falling back to previous days..."
                )
        else:
            log.warning("Could not find next working day within 7 days. Falling back to previous days...")
    
    # ── STRATEGY 2: Try today, yesterday, 2 days ago, 3 days ago ─────────────
    for days_back in range(4):
        attempt_date = dt - timedelta(days=days_back)
        if days_back > 0:
            log.info(
                f"Attempting fallback download for {attempt_date.strftime('%d-%b-%Y')} "
                f"({days_back} days back)..."
            )
        
        success, files_status = await _attempt_download(attempt_date)
        
        if success:
            # Save to database
            try:
                await _save_span_to_db(_store.span, original_date)
                await _save_elm_to_db(_store.elm_oth, _store.elm_otm, original_date)
                await _log_download_attempt(
                    original_date,
                    "success" if days_back == 0 else "fallback",
                    len(_store.span),
                    len(_store.elm_oth),
                    len(_store.elm_otm),
                    None,
                    files_status,
                )
                
                if days_back > 0:
                    # Notify admin about fallback
                    await _create_system_notification(
                        category="span_download",
                        severity="warning",
                        title=f"SPAN Data: Using {days_back}-day old data",
                        message=f"Fresh SPAN data for {original_date.strftime('%d-%b-%Y')} unavailable. "
                                f"Using data from {attempt_date.strftime('%d-%b-%Y')}.",
                        details={
                            "requested_date": original_date.isoformat(),
                            "actual_date": attempt_date.date().isoformat(),
                            "days_back": days_back,
                            "span_symbols": len(_store.span),
                            "elm_futures": len(_store.elm_oth),
                            "elm_options": len(_store.elm_otm),
                        },
                    )
            except Exception as exc:
                log.error(f"Failed to save SPAN data to database: {exc}")
            
            return True
    
    # All download attempts failed — try loading from database
    log.error(
        f"All download attempts failed for {original_date.strftime('%d-%b-%Y')} "
        "and previous 3 days. Attempting to load from database cache..."
    )
    
    db_loaded = await _load_latest_from_db()
    
    if db_loaded:
        await _create_system_notification(
            category="span_download",
            severity="warning",
            title="SPAN Data: Using Database Cache",
            message=f"NSE SPAN download failed for {original_date.strftime('%d-%b-%Y')}. "
                    f"Using cached data from {_store.as_of.strftime('%d-%b-%Y') if _store.as_of else 'unknown'}.",
            details={
                "requested_date": original_date.isoformat(),
                "cached_date": _store.as_of.isoformat() if _store.as_of else None,
                "span_symbols": len(_store.span),
                "elm_futures": len(_store.elm_oth),
                "elm_options": len(_store.elm_otm),
            },
        )
        log.warning("Using cached SPAN data from database")
        return False
    else:
        # Complete failure
        await _create_system_notification(
            category="span_download",
            severity="critical",
            title="SPAN Data Download Failed",
            message=f"Failed to download SPAN data for {original_date.strftime('%d-%b-%Y')} "
                    "Database cache will be used perpetually until fresh data becomes available.",
            details={
                "requested_date": original_date.isoformat(),
                "attempts": 4,
            },
        )
        await _log_download_attempt(
            original_date,
            "failed",
            0,
            0,
            0,
            "All download attempts failed and no database cache available",
            None,
        )
        log.error("SPAN data completely unavailable — margin calculations will use fallback rates")
        return False


async def _attempt_download(dt: datetime) -> tuple[bool, dict]:
    """
    Attempt to download SPAN data for a specific date.
    
    Returns:
        (success: bool, files_status: dict)
    """
    client = await _get_session_client()
    success = True
    files_status = {"ael": False, "fo_span": False, "com_span": False}

    try:
        # ── 1.  Exposure Limit CSV ─────────────────────────────────────────
        ael_content = await _download_ael_csv(client, dt)
        if ael_content:
            ael_map = _parse_ael_csv(ael_content)
            # Update store
            _store.elm_oth.clear()
            _store.elm_otm.clear()
            for sym, types in ael_map.items():
                if "OTH" in types:
                    _store.elm_oth[sym] = types["OTH"]
                if "OTM" in types:
                    _store.elm_otm[sym] = types["OTM"]
            files_status["ael"] = True
        else:
            log.warning("AEL download failed")
            success = False

        # ── 2.  F&O SPAN® ─────────────────────────────────────────────────
        fo_url  = _FO_SPAN_URL.replace("{YYYYMMDD}", _date_yyyymmdd(dt))
        fo_spn  = await _download_span_zip(client, fo_url, "F&O")
        if fo_spn:
            fo_map  = _parse_span_xml(fo_spn, "fo")
            _store.span.update(fo_map)
            files_status["fo_span"] = True
        else:
            log.warning("F&O SPAN download failed")
            success = False

        # ── 3.  COM SPAN® ─────────────────────────────────────────────────
        com_url = _COM_SPAN_URL.replace("{YYYYMMDD}", _date_yyyymmdd(dt))
        com_spn = await _download_span_zip(client, com_url, "COM")
        if com_spn:
            com_map = _parse_span_xml(com_spn, "com")
            _store.span.update(com_map)     # merge (commodity symbols don't clash)
            files_status["com_span"] = True
        else:
            log.warning("COM SPAN download failed")
            success = False

    finally:
        await client.aclose()

    if _store.span or _store.elm_oth:
        _store.as_of = dt
        _store.ready = True
        log.info(
            f"NSE margin store updated: "
            f"{len(_store.span)} SPAN symbols, "
            f"{len(_store.elm_oth)} ELM-OTH, "
            f"{len(_store.elm_otm)} ELM-OTM  "
            f"[{dt.strftime('%d-%b-%Y')}]"
        )
        # Return success only if we got at least some data
        return (success and any(files_status.values()), files_status)
    else:
        log.error("NSE margin download produced no data")
        return (False, files_status)


# ── Daily Scheduler ───────────────────────────────────────────────────────────

class NseMarginScheduler:
    """
    Triggers download_and_refresh() at 08:45 IST each trading day.
    This ensures margin data is fresh before NSE opens at 09:15 IST.

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
            log.warning("NseMarginScheduler already running")
            return
        self._task = asyncio.create_task(self._loop(), name="nse_margin_scheduler")
        log.info(
            f"NseMarginScheduler started — refreshes at "
            f"{self.REFRESH_HOUR:02d}:{self.REFRESH_MINUTE:02d} IST daily."
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("NseMarginScheduler stopped.")

    async def _loop(self) -> None:
        while True:
            try:
                wait = self._seconds_until_next_refresh()
                log.info(
                    f"NseMarginScheduler: next refresh in "
                    f"{int(wait // 3600)}h {int((wait % 3600) // 60)}m."
                )
                await asyncio.sleep(wait)

                # Attempt download with retries
                for attempt in range(1, self.MAX_RETRIES + 1):
                    log.info(
                        f"NseMarginScheduler: refresh attempt {attempt}/{self.MAX_RETRIES} …"
                    )
                    ok = await download_and_refresh()
                    if ok:
                        self.last_run_at = datetime.now(IST)
                        self.last_run_success = True
                        self.last_run_error = None
                        break
                    if attempt < self.MAX_RETRIES:
                        log.warning(
                            f"Margin refresh attempt {attempt} failed; "
                            f"retrying in {self.RETRY_INTERVAL // 60}m …"
                        )
                        await asyncio.sleep(self.RETRY_INTERVAL)
                else:
                    self.last_run_at = datetime.now(IST)
                    self.last_run_success = False
                    self.last_run_error = "All retry attempts exhausted"
                    log.error(
                        "All NSE margin refresh attempts failed today; "
                        "carrying forward previous data until tomorrow."
                    )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_run_at = datetime.now(IST)
                self.last_run_success = False
                self.last_run_error = str(exc)
                log.exception(f"NseMarginScheduler error: {exc}. Retrying in 1 hour.")
                await asyncio.sleep(3600)

    def _seconds_until_next_refresh(self) -> float:
        now  = datetime.now(IST)
        next_refresh = now.replace(
            hour=self.REFRESH_HOUR,
            minute=self.REFRESH_MINUTE,
            second=0,
            microsecond=0,
        )
        if now >= next_refresh:
            next_refresh = next_refresh + timedelta(days=1)
        return (next_refresh - now).total_seconds()


nse_margin_scheduler = NseMarginScheduler()
