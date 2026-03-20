"""
app/instruments/scrip_master.py
================================
Single source of truth for all instrument data.

Startup flow
------------
1. `seed_subscription_lists_if_empty()`
       If the subscription_lists table is empty, seed it from the 6 local CSV
       files in instrument_master/, mapping display names to UNDERLYING_SYMBOL
       values using the local scrip master CSV as a lookup.

2. `refresh_instruments()`
       Loads subscription lists from DB, downloads the latest scrip master CSV
       from the DhanHQ CDN (or reads the local file in dev-mode), classifies
       every row, then bulk-upserts instrument_master.

Daily scheduler
---------------
`ScripMasterScheduler` runs `refresh_instruments()` at 06:00 IST each day.

Admin API
---------
`get_list_csv(list_name)` → CSV bytes (for download endpoint)
`replace_list_from_csv(list_name, content)` → replaces rows and reclassifies
"""
import asyncio
import csv
import io
import logging
import os
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx

from app.database import get_pool

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

CDN_URL   = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
_DEFAULT_LOCAL_CSV = Path(__file__).parent.parent.parent / "instrument_master" / "api-scrip-master-detailed.csv"
LOCAL_CSV = Path(os.getenv("INSTRUMENTS_CSV_PATH", str(_DEFAULT_LOCAL_CSV)))
IST = timezone(timedelta(hours=5, minutes=30))

# The 6 list names (canonical, stored in DB)
LIST_EQUITY          = "equity"
LIST_OPTIONS_STOCKS  = "options_stocks"
LIST_FUTURES_STOCKS  = "futures_stocks"
LIST_ETF             = "etf"
LIST_MCX_FUTURES     = "mcx_futures"
LIST_MCX_OPTIONS     = "mcx_options"
ALL_LISTS = [
    LIST_EQUITY,
    LIST_OPTIONS_STOCKS,
    LIST_FUTURES_STOCKS,
    LIST_ETF,
    LIST_MCX_FUTURES,
    LIST_MCX_OPTIONS,
]

# Local CSV files for initial seeding  (list_name → path, name_column, is_display_name)
_LOCAL_LIST_FILES = {
    # equity-list.csv uses SYMBOL as the underlying symbol (not display name)
    LIST_EQUITY:         ("equity-list.csv",         "SYMBOL",    False),
    LIST_OPTIONS_STOCKS: ("options-stocks-list.csv",  "Name",      True),
    LIST_FUTURES_STOCKS: ("futures-stocks-list.csv",  "Name",      True),
    LIST_ETF:            ("etf-list.csv",             "Name",      True),
    LIST_MCX_FUTURES:    ("mcx-comm-futures.csv",     "Commodity", False),  # already the symbol
    LIST_MCX_OPTIONS:    ("mcx-comm-options.csv",     "Commodity", False),
}

# Index underlyings tracked for LTP / INDEX spot / FUTIDX
_TIER_B_INDICES = {"NIFTY", "BANKNIFTY", "SENSEX", "MIDCPNIFTY", "FINNIFTY", "BANKEX", "NIFTYNXT50"}

# Index underlyings whose OPTIONS (OPTIDX) are Tier-B (always subscribed).
# All other index options (MIDCPNIFTY, FINNIFTY, BANKEX, NIFTYNXT50) are Tier-A
# (on-demand) to keep DhanHQ WebSocket subscription counts within the 25 000 limit.
_TIER_B_OPTIDX = {"NIFTY", "BANKNIFTY", "SENSEX"}


def _ws_slot(token: int) -> int:
    """Deterministic WS slot 0-4 — same token always maps to same slot."""
    return token % 5


# ── Classification ───────────────────────────────────────────────────────────

def _classify(row: dict, lists: dict[str, set[str]]) -> Optional[str]:
    """
    Returns 'A' (on-demand) or 'B' (always-subscribed), or None (exclude).
    All comparisons use the UNDERLYING_SYMBOL column (uppercased).
    """
    itype      = (row.get("INSTRUMENT") or "").strip().upper()
    underlying = (row.get("UNDERLYING_SYMBOL") or "").strip().upper()
    exch_id    = (row.get("EXCH_ID") or "").strip().upper()
    seg_code   = (row.get("SEGMENT") or "").strip().upper()
    # Get symbol for INDEX matching (INDEX instruments use symbol field, not underlying)
    symbol     = (row.get("DISPLAY_NAME") or row.get("SYMBOL_NAME") or "").strip().upper()

    # ── Index spot — always Tier-B ──────────────────────────────────────────
    # INDEX instruments for NIFTY, BANKNIFTY, SENSEX etc. must be subscribed
    # for live LTP updates used in ATM calculations and frontend displays.
    if itype == "INDEX" and symbol in _TIER_B_INDICES:
        if exch_id == "NSE":
            return "B"
        if exch_id == "BSE" and symbol in {"SENSEX", "BANKEX"}:
            return "B"

    # ── Index futures — always Tier-B for all tracked indices ─────────────────
    if itype == "FUTIDX" and underlying in _TIER_B_INDICES and seg_code == "D":
        if exch_id == "NSE":
            return "B"
        if exch_id == "BSE" and underlying in {"SENSEX", "BANKEX"}:
            return "B"

    # ── Index options — Tier-B only for NIFTY / BANKNIFTY / SENSEX ──────────
    # MIDCPNIFTY, FINNIFTY, BANKEX, NIFTYNXT50 options → Tier-A (on-demand)
    # to keep DhanHQ WebSocket subscriptions well below the 25 000 limit.
    if itype == "OPTIDX" and seg_code == "D":
        if underlying in _TIER_B_OPTIDX and exch_id == "NSE":
            return "B"
        if underlying == "SENSEX" and exch_id == "BSE":
            return "B"
        # All remaining index options (MIDCPNIFTY, FINNIFTY, BANKEX, NIFTYNXT50) → on-demand
        if underlying in _TIER_B_INDICES and exch_id in ("NSE", "BSE"):
            return "A"

    # ── Stock options — Tier-A (on-demand) ──────────────────────────────────
    if itype == "OPTSTK" and underlying in lists[LIST_OPTIONS_STOCKS] and exch_id == "NSE" and seg_code == "D":
        return "A"

    # ── Stock futures — Tier-B ───────────────────────────────────────────────
    if itype == "FUTSTK" and underlying in lists[LIST_FUTURES_STOCKS] and exch_id == "NSE" and seg_code == "D":
        return "B"

    # ── Equity cash — Tier-A (on-demand) ────────────────────────────────────
    if itype == "EQUITY" and underlying in lists[LIST_EQUITY] and exch_id == "NSE" and seg_code == "E":
        return "A"

    # ── ETF — Tier-B ─────────────────────────────────────────────────────────
    # Dhan scrip master typically represents ETFs as INSTRUMENT=EQUITY (cash seg).
    if itype in ("ETF", "EQUITY") and underlying in lists[LIST_ETF] and exch_id == "NSE" and seg_code == "E":
        return "B"

    # ── MCX commodity futures — Tier-B ───────────────────────────────────────
    if itype == "FUTCOM" and underlying in lists[LIST_MCX_FUTURES] and exch_id == "MCX" and seg_code == "M":
        return "B"

    # ── MCX commodity options — Tier-B ───────────────────────────────────────
    if itype in ("OPTFUT", "OPTCOM") and underlying in lists[LIST_MCX_OPTIONS] and exch_id == "MCX" and seg_code == "M":
        return "B"

    return None   # not in scope


# ── Row parsing ──────────────────────────────────────────────────────────────

def _parse_expiry(val: str) -> Optional[date]:
    val = (val or "").strip()
    if not val or val in ("NA", "-"):
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def _build_record(row: dict, tier: str) -> tuple:
    """
    Returns a tuple in INSERT column order:
    (token, security_id, exchange_segment, symbol, underlying, itype, expiry, strike,
     opt_type, tick_size, lot_size, tier, ws_slot, isin, display_name, series)

    CSV Column Mapping:
      symbol DB field    ← DISPLAY_NAME (user-friendly searchable name, e.g. "MARUTI SUZUKI INDIA LTD")
      underlying DB field ← UNDERLYING_SYMBOL (base asset, e.g. "MARUTI", "NIFTY")
      display_name DB field ← SYMBOL_NAME (the trading ticker for reference, e.g. "MARUTI")
    """
    token    = int(row["SECURITY_ID"])
    security_id = token
    exch_id  = (row.get("EXCH_ID") or "").strip().upper()
    seg_code = (row.get("SEGMENT") or "").strip().upper()
    segment  = _map_exchange_segment(exch_id, seg_code)
    # Use DISPLAY_NAME for searchable symbol field (user-friendly names like "MARUTI SUZUKI INDIA LTD")
    # Fallback to SYMBOL_NAME if DISPLAY_NAME is missing (backward compatibility)
    symbol   = (row.get("DISPLAY_NAME") or row.get("SYMBOL_NAME") or "").strip()
    # Underlying symbol for options/futures. Use UNDERLYING_SYMBOL (e.g. "NIFTY")
    # Fallback to symbol if UNDERLYING_SYMBOL is missing (for equities where underlying = symbol)
    under    = (row.get("UNDERLYING_SYMBOL") or symbol).strip().upper()
    itype    = (row.get("INSTRUMENT") or "").strip().upper()
    expiry   = _parse_expiry(row.get("SM_EXPIRY_DATE", ""))
    strike_s = (row.get("STRIKE_PRICE") or "").strip()
    strike   = float(strike_s) if strike_s and strike_s not in ("", "NA") else None
    opt_type = (row.get("OPTION_TYPE") or "").strip().upper() or None
    tick_s   = (row.get("TICK_SIZE") or "0.05").strip()
    tick     = float(tick_s) if tick_s else 0.05
    lot_s    = (row.get("LOT_SIZE") or "1").strip()
    lot      = int(float(lot_s)) if lot_s else 1
    isin     = (row.get("ISIN") or "").strip() or None
    # Store SYMBOL_NAME in display_name for reference (the actual trading ticker symbol)
    # Fallback to DISPLAY_NAME if SYMBOL_NAME is missing for consistency
    display  = (row.get("SYMBOL_NAME") or row.get("DISPLAY_NAME") or "").strip() or None
    series   = (row.get("SERIES") or "").strip() or None
    ws       = _ws_slot(token) if tier == "B" else None

    return (token, security_id, segment, symbol, under, itype,
            expiry, strike, opt_type, tick, lot,
            tier, ws, isin, display, series)


def _map_exchange_segment(exch_id: str, seg_code: str) -> str:
    """Convert CSV EXCH_ID+SEGMENT to Dhan API ExchangeSegment values."""
    # NSE
    if exch_id == "NSE" and seg_code == "E":
        return "NSE_EQ"
    if exch_id == "NSE" and seg_code == "D":
        return "NSE_FNO"
    if exch_id == "NSE" and seg_code == "C":
        return "NSE_CURR"

    # BSE
    if exch_id == "BSE" and seg_code == "E":
        return "BSE_EQ"
    if exch_id == "BSE" and seg_code == "D":
        return "BSE_FNO"
    if exch_id == "BSE" and seg_code == "C":
        return "BSE_CURR"

    # MCX
    if exch_id == "MCX" and seg_code == "M":
        return "MCX_COMM"

    return f"{exch_id}_{seg_code}".strip("_")


# ── Subscription lists — DB I/O ──────────────────────────────────────────────

async def load_subscription_lists_from_db() -> dict[str, set[str]]:
    """Returns mapping list_name → set of uppercase UNDERLYING_SYMBOL."""
    pool = get_pool()
    rows = await pool.fetch("SELECT list_name, symbol FROM subscription_lists")
    result: dict[str, set[str]] = {name: set() for name in ALL_LISTS}
    for r in rows:
        ln = r["list_name"]
        if ln in result:
            result[ln].add(r["symbol"].upper())
    return result


async def seed_subscription_lists_if_empty() -> None:
    """
    If subscription_lists table is empty, seed it from the 6 local CSV files.
    For files that contain display names, they are resolved to UNDERLYING_SYMBOL
    using the local scrip master CSV as a lookup dictionary.
    Runs once. Subsequent seeds are done via the admin upload endpoint.
    """
    pool  = get_pool()
    # Backfill any missing list_name(s). This keeps existing installs working
    # even if earlier versions only seeded a subset of lists.
    counts_rows = await pool.fetch(
        "SELECT list_name, COUNT(*)::int AS c FROM subscription_lists GROUP BY list_name"
    )
    counts = {r["list_name"]: int(r["c"] or 0) for r in counts_rows}
    total = sum(counts.values())
    if total > 0 and all((counts.get(name, 0) > 0) for name in ALL_LISTS):
        log.info(f"Subscription lists already seeded ({total} rows) — skipping.")
        return

    if not LOCAL_CSV.exists():
        log.warning(
            "Local scrip master CSV not found — cannot seed subscription lists. "
            f"Expected: {LOCAL_CSV}"
        )
        return

    log.info("Seeding subscription lists from local CSV files…")

    # Build DISPLAY_NAME.upper() → UNDERLYING_SYMBOL from local master CSV
    # This maps user-friendly names to base asset symbols for subscription list matching
    display_to_symbol: dict[str, str] = {}
    with open(LOCAL_CSV, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            dn  = (row.get("DISPLAY_NAME") or "").strip()
            sym = (row.get("UNDERLYING_SYMBOL") or "").strip().upper()
            if dn and sym:
                display_to_symbol[dn.upper()] = sym

    list_dir = LOCAL_CSV.parent
    rows_to_insert: list[tuple[str, str]] = []

    for list_name, (filename, col, is_display_name) in _LOCAL_LIST_FILES.items():
        if counts.get(list_name, 0) > 0:
            continue
        path = list_dir / filename
        if not path.exists():
            log.warning(f"Subscription list file not found: {path}")
            continue

        matched = 0
        unmatched: list[str] = []

        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                raw = (row.get(col) or "").strip()
                if not raw:
                    continue

                if is_display_name:
                    # Resolve display name → UNDERLYING_SYMBOL
                    symbol = display_to_symbol.get(raw.upper())
                    if not symbol:
                        unmatched.append(raw)
                        continue
                else:
                    # MCX lists: value IS the underlying symbol
                    symbol = raw.upper()

                rows_to_insert.append((list_name, symbol))
                matched += 1

        log.info(f"  {list_name}: {matched} matched" +
                 (f", {len(unmatched)} unmatched" if unmatched else ""))
        if unmatched:
            log.debug(f"  {list_name} unmatched: {unmatched[:10]}")

    if rows_to_insert:
        await pool.executemany(
            "INSERT INTO subscription_lists (list_name, symbol) VALUES ($1, $2) "
            "ON CONFLICT (list_name, symbol) DO NOTHING",
            rows_to_insert,
        )
        log.info(f"Subscription lists seeded — {len(rows_to_insert)} rows inserted.")


# ── Instrument upsert ────────────────────────────────────────────────────────

_UPSERT_SQL = """
INSERT INTO instrument_master
    (instrument_token, security_id, exchange_segment, symbol, underlying,
     instrument_type, expiry_date, strike_price, option_type,
     tick_size, lot_size, tier, ws_slot, isin, display_name, series)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
ON CONFLICT (instrument_token) DO UPDATE SET
    security_id      = EXCLUDED.security_id,
    exchange_segment = EXCLUDED.exchange_segment,
    symbol           = EXCLUDED.symbol,
    underlying       = EXCLUDED.underlying,
    instrument_type  = EXCLUDED.instrument_type,
    expiry_date      = EXCLUDED.expiry_date,
    strike_price     = EXCLUDED.strike_price,
    option_type      = EXCLUDED.option_type,
    tick_size        = EXCLUDED.tick_size,
    lot_size         = EXCLUDED.lot_size,
    tier             = EXCLUDED.tier,
    ws_slot          = EXCLUDED.ws_slot,
    isin             = EXCLUDED.isin,
    display_name     = EXCLUDED.display_name,
    series           = EXCLUDED.series
"""

async def _upsert_batch(pool, batch: list[tuple]) -> None:
    async with pool.acquire() as conn:
        await conn.executemany(_UPSERT_SQL, batch)


# ── Core refresh logic ───────────────────────────────────────────────────────

async def _refresh_from_content(content: str) -> None:
    """
    Parse CSV content, classify every row using DB subscription lists,
    and bulk-upsert into instrument_master.
    """
    lists = await load_subscription_lists_from_db()
    pool  = get_pool()

    # Strip trailing comma that Dhan adds to every row.
    # Some local copies of the CSV are wrapped mid-header (e.g. newline after SERIES,),
    # which breaks csv.DictReader. Detect and re-join header fragments.
    lines = [line.rstrip(",") for line in content.splitlines() if line is not None]
    if lines and lines[0].startswith("EXCH_ID") and "LOT_SIZE" not in lines[0]:
        # Join up to the next few lines until we have the full header.
        join_guard = 0
        while join_guard < 5 and len(lines) > 1 and "LOT_SIZE" not in lines[0]:
            nxt = lines[1].strip().lstrip(",")
            # Only join if the next line looks like a header continuation (not a data row).
            if nxt.startswith("LOT_SIZE") or nxt.startswith("SM_EXPIRY_DATE") or nxt.startswith("STRIKE_PRICE"):
                lines[0] = (lines[0].rstrip(",") + "," + nxt).strip(",")
                del lines[1]
                join_guard += 1
                continue
            break

    clean = "\n".join(line for line in lines if line.strip() != "")

    reader = csv.DictReader(io.StringIO(clean))
    batch: list[tuple] = []
    skipped = 0

    for row in reader:
        try:
            token_s = (row.get("SECURITY_ID") or "").strip()
            if not token_s or token_s == "0":
                continue

            tier = _classify(row, lists)
            if tier is None:
                skipped += 1
                continue

            batch.append(_build_record(row, tier))

            if len(batch) >= 2000:
                await _upsert_batch(pool, batch)
                batch.clear()

        except Exception as exc:
            symbol_info = row.get('DISPLAY_NAME') or row.get('SYMBOL_NAME') or '?'
            log.debug(f"Skipping row {symbol_info}: {exc}")

    if batch:
        await _upsert_batch(pool, batch)

    total = await pool.fetchval("SELECT COUNT(*) FROM instrument_master")
    tier_b = await pool.fetchval("SELECT COUNT(*) FROM instrument_master WHERE tier='B'")
    tier_a = await pool.fetchval("SELECT COUNT(*) FROM instrument_master WHERE tier='A'")
    log.info(
        f"instrument_master refreshed — "
        f"total: {total}, Tier-B: {tier_b}, Tier-A: {tier_a}, skipped: {skipped}"
    )

    # Record timestamp
    await pool.execute(
        "UPDATE system_config SET value=$1, updated_at=now() WHERE key='scrip_master_refreshed_at'",
        datetime.now(IST).isoformat(),
    )


async def refresh_instruments(download: bool = True) -> None:
    """
    Main entry point.
    download=True  → fetch fresh CSV from DhanHQ CDN (production)
    download=False → read local CSV file (dev mode / first run fallback)
    """
    if download:
        log.info(f"Downloading scrip master from {CDN_URL} …")
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                resp = await client.get(CDN_URL)
                resp.raise_for_status()
            content = resp.text
            log.info(f"Downloaded {len(content):,} characters.")
        except Exception as exc:
            log.error(f"CDN download failed: {exc} — falling back to local CSV.")
            download = False

    if not download:
        if not LOCAL_CSV.exists():
            log.warning(
                f"Local scrip master CSV not found at {LOCAL_CSV}. "
                "Instrument master will be empty."
            )
            return
        log.info(f"Loading instrument master from local CSV: {LOCAL_CSV}")
        content = LOCAL_CSV.read_text(encoding="utf-8", errors="replace")

    await _refresh_from_content(content)


# ── Admin helpers ─────────────────────────────────────────────────────────────

async def get_list_as_csv(list_name: str) -> str:
    """Returns current DB subscription list as a CSV string (symbol per row)."""
    if list_name not in ALL_LISTS:
        raise ValueError(f"Unknown list: {list_name}")
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT symbol FROM subscription_lists WHERE list_name=$1 ORDER BY symbol",
        list_name,
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["symbol"])
    for r in rows:
        w.writerow([r["symbol"]])
    return buf.getvalue()


async def replace_list_from_csv(list_name: str, content: str) -> int:
    """
    Replace all rows for list_name with symbols from an uploaded CSV.
    CSV must have a 'symbol' header column (UNDERLYING_SYMBOL, uppercase).
    Triggers reclassification of the full instrument_master afterward.
    Returns the number of symbols imported.
    """
    if list_name not in ALL_LISTS:
        raise ValueError(f"Unknown list: {list_name}")

    reader  = csv.DictReader(io.StringIO(content))
    symbols = [
        (list_name, (row.get("symbol") or "").strip().upper())
        for row in reader
        if (row.get("symbol") or "").strip()
    ]

    if not symbols:
        raise ValueError("No valid symbols found in uploaded CSV.")

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM subscription_lists WHERE list_name=$1", list_name
            )
            await conn.executemany(
                "INSERT INTO subscription_lists (list_name, symbol) VALUES ($1, $2) "
                "ON CONFLICT (list_name, symbol) DO NOTHING",
                symbols,
            )

    log.info(f"Replaced {list_name} list with {len(symbols)} symbols. Re-classifying…")

    # Re-classify instrument_master with updated lists
    # (use local file if download is disabled, otherwise skip full re-download
    #  and just re-classify from existing table content in a lighter pass)
    await _reclassify_in_place()

    return len(symbols)


async def _reclassify_in_place() -> None:
    """
    Re-evaluate tier/ws_slot for every row already in instrument_master
    using the current subscription lists, without re-downloading the CSV.
    This is a fast in-DB operation only — used after an admin list upload.
    Rows not in any list are set to tier=NULL (will be excluded by queries).
    """
    lists = await load_subscription_lists_from_db()
    pool  = get_pool()

    # Build tier_b and tier_a sets from lists
    tier_b_types_and_symbols: list[tuple[str, str]] = []
    tier_a_types_and_symbols: list[tuple[str, str]] = []

    # Index types always B (no list)
    # We'll handle the rest via SQL using the lists + instrument_type

    # Update Tier-B index spot instruments (INDEX type) — always subscribed for live LTP
    tier_b_indices = list(_TIER_B_INDICES)
    await pool.execute(
        """UPDATE instrument_master SET tier='B', ws_slot=(instrument_token % 5)
           WHERE instrument_type = 'INDEX'
             AND symbol = ANY($1::text[])""",
        tier_b_indices,
    )

    # Update Tier-B index futures (FUTIDX) — all tracked indices always subscribed
    await pool.execute(
        """UPDATE instrument_master SET tier='B', ws_slot=(instrument_token % 5)
           WHERE instrument_type = 'FUTIDX'
             AND underlying = ANY($1::text[])""",
        tier_b_indices,
    )

    # Update Tier-B index options (OPTIDX) — NIFTY, BANKNIFTY, SENSEX only
    tier_b_optidx = list(_TIER_B_OPTIDX)
    await pool.execute(
        """UPDATE instrument_master SET tier='B', ws_slot=(instrument_token % 5)
           WHERE instrument_type = 'OPTIDX'
             AND underlying = ANY($1::text[])""",
        tier_b_optidx,
    )

    # Demote remaining index options to Tier-A (MIDCPNIFTY, FINNIFTY, BANKEX, NIFTYNXT50)
    tier_a_optidx = [idx for idx in tier_b_indices if idx not in _TIER_B_OPTIDX]
    await pool.execute(
        """UPDATE instrument_master SET tier='A', ws_slot=NULL
           WHERE instrument_type = 'OPTIDX'
             AND underlying = ANY($1::text[])""",
        tier_a_optidx,
    )

    # Update each list
    list_updates = [
        (LIST_EQUITY,          ["EQUITY"],          "A"),
        (LIST_OPTIONS_STOCKS,  ["OPTSTK"],           "A"),
        (LIST_FUTURES_STOCKS,  ["FUTSTK"],           "B"),
        (LIST_ETF,             ["ETF"],              "B"),
        (LIST_MCX_FUTURES,     ["FUTCOM"],           "B"),
        (LIST_MCX_OPTIONS,     ["OPTFUT", "OPTCOM"], "B"),
    ]

    for list_name, itypes, tier in list_updates:
        symbols = list(lists[list_name])
        if not symbols:
            continue
        ws_expr = "(instrument_token % 5)" if tier == "B" else "NULL"
        await pool.execute(
            f"""UPDATE instrument_master
                SET tier=$1, ws_slot={ws_expr}
                WHERE instrument_type = ANY($2::text[])
                  AND underlying = ANY($3::text[])""",
            tier, itypes, symbols,
        )

    log.info("In-place re-classification complete.")


# ── Daily scheduler ───────────────────────────────────────────────────────────

class ScripMasterScheduler:
    """Triggers refresh_instruments() at 06:00 IST every day."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self.last_run_at: Optional[datetime] = None
        self.last_run_error: Optional[str] = None
        self.last_instrument_count: Optional[int] = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            log.warning("ScripMasterScheduler already running")
            return
        self._task = asyncio.create_task(self._loop(), name="scrip_master_scheduler")
        log.info("ScripMasterScheduler started — will refresh at 06:00 IST daily.")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("ScripMasterScheduler stopped.")

    async def _loop(self) -> None:
        while True:
            try:
                seconds = self._seconds_until_next_6am_ist()
                log.info(
                    f"ScripMasterScheduler: next refresh in "
                    f"{seconds // 3600}h {(seconds % 3600) // 60}m."
                )
                await asyncio.sleep(seconds)
                log.info("ScripMasterScheduler: triggering daily scrip master refresh…")
                await refresh_instruments(download=True)
                self.last_run_at = datetime.now(IST)
                self.last_run_error = None
                # Evict expired contracts from active WS subscriptions right
                # after the scrip master is refreshed with today's data.
                try:
                    from app.instruments.subscription_manager import handle_expiry_rollover
                    await handle_expiry_rollover()
                except Exception as exc:
                    log.error(f"ScripMasterScheduler: expiry rollover failed: {exc}")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.last_run_at = datetime.now(IST)
                self.last_run_error = str(exc)
                log.exception(f"ScripMasterScheduler error: {exc}. Retrying in 1 hour.")
                await asyncio.sleep(3600)

    @staticmethod
    def _seconds_until_next_6am_ist() -> float:
        now   = datetime.now(IST)
        next6 = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= next6:
            next6 = next6 + timedelta(days=1)
        return (next6 - now).total_seconds()


scrip_scheduler = ScripMasterScheduler()
