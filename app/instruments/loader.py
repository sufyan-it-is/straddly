"""
app/instruments/loader.py
==========================
Loads instrument data from DhanHQ scrip master CSV into instrument_master table.
Classifies each instrument as Tier-A or Tier-B and assigns ws_slot.

Tier-B (always subscribed):
  - NSE Index Options (NIFTY, BANKNIFTY, SENSEX)
  - NSE Stock Futures
  - NSE Equity Cash
  - MCX Futures
  - MCX Options

Tier-A (on-demand — stock options):
  - NSE Stock Options (208 stocks × 2 expiries × 51 strikes × 2 = ~42k tokens)

WS slot assignment (Tier-B):
  ws_slot = hash(instrument_token) % 5  →  deterministic, even distribution
"""
import csv
import hashlib
import logging
from pathlib import Path
from datetime import date, datetime

import asyncpg

from app.database import get_pool

log = logging.getLogger(__name__)

# ── Paths ───────────────────────────────────────────────────────────────────
MASTER_DIR = Path(__file__).parent.parent.parent / "instrument_master"

OPTIONS_STOCKS_CSV  = MASTER_DIR / "options-stocks-list.csv"
FUTURES_STOCKS_CSV  = MASTER_DIR / "futures-stocks-list.csv"
EQUITY_CSV          = MASTER_DIR / "equity-list.csv"
MCX_FUTURES_CSV     = MASTER_DIR / "mcx-comm-futures.csv"
MCX_OPTIONS_CSV     = MASTER_DIR / "mcx-comm-options.csv"
SCRIP_MASTER_CSV    = MASTER_DIR / "api-scrip-master-detailed.csv"

# Index underlyings that are always Tier-B
_TIER_B_INDICES = {"NIFTY", "BANKNIFTY", "SENSEX"}

# 3-expiry commodities (GOLD & SILVER families)
_THREE_EXPIRY_COMMODITIES = {
    "GOLD", "GOLDM", "GOLDGUINEA", "GOLDPETAL", "GOLDTEN",
    "SILVER", "SILVERM", "SILVERMIC",
}


def _ws_slot(instrument_token: int) -> int:
    """Deterministic slot 0-4 via modulo — same token always maps to same WS."""
    return instrument_token % 5


def _read_name_list(path: Path) -> set[str]:
    """Read a single-column CSV (header 'Name' or 'Commodity') into a set."""
    names: set[str] = set()
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            val = (row.get("Name") or row.get("Commodity") or "").strip()
            if val:
                names.add(val.upper())
    return names


async def load_instruments() -> None:
    """
    Parse the scrip master CSV and upsert all relevant instruments into
    instrument_master. Sets tier and ws_slot.
    Called once at startup (idempotent — uses ON CONFLICT DO UPDATE).
    """
    if not SCRIP_MASTER_CSV.exists():
        log.warning(
            f"Scrip master CSV not found at {SCRIP_MASTER_CSV}. "
            "Instrument master will be empty. Download from DhanHQ."
        )
        return

    options_stocks  = _read_name_list(OPTIONS_STOCKS_CSV)
    futures_stocks  = _read_name_list(FUTURES_STOCKS_CSV)
    equity_symbols  = _read_name_list(EQUITY_CSV)
    mcx_futures_com = _read_name_list(MCX_FUTURES_CSV)
    mcx_options_com = _read_name_list(MCX_OPTIONS_CSV)

    pool  = get_pool()
    batch: list[tuple] = []

    with open(SCRIP_MASTER_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rec = _classify_row(
                row,
                options_stocks,
                futures_stocks,
                equity_symbols,
                mcx_futures_com,
                mcx_options_com,
            )
            if rec:
                batch.append(rec)

    if not batch:
        log.warning("No instruments matched classification rules.")
        return

    log.info(f"Upserting {len(batch)} instruments into instrument_master…")
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO instrument_master
                (instrument_token, security_id, exchange_segment, symbol, underlying,
                 instrument_type, expiry_date, strike_price, option_type,
                 tick_size, lot_size, tier, ws_slot)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
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
                ws_slot          = EXCLUDED.ws_slot
            """,
            batch,
        )

    tier_a = sum(1 for r in batch if r[10] == "A")
    tier_b = sum(1 for r in batch if r[10] == "B")
    log.info(f"Instruments loaded — Tier-B: {tier_b}, Tier-A: {tier_a}")


def _classify_row(
    row: dict,
    options_stocks: set,
    futures_stocks: set,
    equity_symbols: set,
    mcx_futures: set,
    mcx_options: set,
) -> tuple | None:
    """
    Returns a tuple ready for INSERT or None if the instrument is not in scope.
    Tuple order: (token, segment, symbol, underlying, instr_type, expiry,
                  strike, opt_type, tick_size, lot_size, tier, ws_slot)
    """
    try:
        token    = int(row.get("SEM_SMST_SECURITY_ID", 0))
        segment  = (row.get("SEM_EXM_EXCH_ID", "") + "_" +
                    row.get("SEM_SEGMENT", "")).strip("_")
        symbol   = row.get("SEM_TRADING_SYMBOL", "").strip()
        itype    = row.get("SEM_INSTRUMENT_NAME", "").strip().upper()
        underlying = row.get("SEM_CUSTOM_SYMBOL", symbol).strip().upper()

        expiry_str   = row.get("SEM_EXPIRY_DATE", "") or ""
        expiry: date | None = None
        if expiry_str.strip():
            try:
                expiry = datetime.strptime(expiry_str.strip(), "%Y-%m-%d").date()
            except ValueError:
                expiry = None

        strike_str = row.get("SEM_STRIKE_PRICE", "") or ""
        strike = float(strike_str) if strike_str.strip() else None
        opt_type = (row.get("SEM_OPTION_TYPE", "") or "").strip().upper() or None

        tick_str = row.get("SEM_TICK_SIZE", "0.05") or "0.05"
        tick_size = float(tick_str) if tick_str.strip() else 0.05
        lot_str  = row.get("SEM_LOT_UNITS", "1") or "1"
        lot_size = int(float(lot_str)) if lot_str.strip() else 1

        if token == 0:
            return None

        # ── Classification ─────────────────────────────────────────────────

        # NSE Index Options — Tier-B
        if itype in ("OPTIDX",) and underlying in _TIER_B_INDICES:
                return (token, token, segment, symbol, underlying, itype,
                    expiry, strike, opt_type,
                    tick_size, lot_size, "B", _ws_slot(token))

        # NSE Stock Options — Tier-A (on-demand)
        if itype in ("OPTSTK",) and underlying in options_stocks:
                return (token, token, segment, symbol, underlying, itype,
                    expiry, strike, opt_type,
                    tick_size, lot_size, "A", None)

        # NSE Stock Futures — Tier-B
        if itype in ("FUTSTK",) and underlying in futures_stocks:
                return (token, token, segment, symbol, underlying, itype,
                    expiry, strike, opt_type,
                    tick_size, lot_size, "B", _ws_slot(token))

        # NSE Equity Cash — Tier-A (on-demand)
        if itype in ("EQUITY", "EQ") and underlying in equity_symbols and segment == "NSE_EQ":
                return (token, token, segment, symbol, underlying, itype,
                    None, None, None,
                tick_size, lot_size, "A", None)

        # MCX Futures — Tier-B
        if itype in ("FUTCOM",) and underlying in mcx_futures:
                return (token, token, segment, symbol, underlying, itype,
                    expiry, strike, opt_type,
                    tick_size, lot_size, "B", _ws_slot(token))

        # MCX Options — Tier-B
        if itype in ("OPTFUT",) and underlying in mcx_options:
                return (token, token, segment, symbol, underlying, itype,
                    expiry, strike, opt_type,
                    tick_size, lot_size, "B", _ws_slot(token))

    except Exception as exc:
        log.debug(f"Skipping row {row.get('SEM_TRADING_SYMBOL', '?')}: {exc}")

    return None
