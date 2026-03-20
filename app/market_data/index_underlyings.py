"""
Helpers for resolving index underlyings against instrument_master.

Why this exists:
- Hardcoded security IDs can drift if broker mappings change.
- Some workflows previously used option_chain_data for expiry lookup, which can be stale.
"""

from __future__ import annotations

from datetime import date

from app.database import get_pool

IDX_SEG = "IDX_I"
IDX_UNDERLYINGS = ("NIFTY", "BANKNIFTY", "SENSEX")

# Safety fallback if instrument_master is temporarily unavailable.
_FALLBACK_SECURITY_IDS = {
    "NIFTY": 13,
    "BANKNIFTY": 25,
    "SENSEX": 51,
}


async def resolve_index_security_id(underlying: str) -> int | None:
    """Resolve index security_id from instrument_master, with a safe fallback."""
    ul = (underlying or "").strip().upper()
    if not ul:
        return None

    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT COALESCE(security_id, instrument_token) AS sid
        FROM instrument_master
        WHERE instrument_type = 'INDEX'
          AND UPPER(underlying) = $1
        ORDER BY instrument_token
        LIMIT 1
        """,
        ul,
    )
    if row and row["sid"] is not None:
        return int(row["sid"])

    return _FALLBACK_SECURITY_IDS.get(ul)


async def resolve_nearest_optidx_expiry(underlying: str) -> date | None:
    """Resolve nearest non-expired OPTIDX expiry for an underlying from instrument_master."""
    ul = (underlying or "").strip().upper()
    if not ul:
        return None

    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT MIN(expiry_date) AS expiry
        FROM instrument_master
        WHERE underlying = $1
          AND instrument_type = 'OPTIDX'
          AND expiry_date >= CURRENT_DATE
        """,
        ul,
    )
    if not row:
        return None
    return row["expiry"]
