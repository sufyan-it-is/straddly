"""
app/routers/option_chain.py  (v2 — /options prefix, frontend-compatible shape)

GET  /options/live?underlying=NIFTY&expiry=2026-02-27
GET  /options/available/expiries?underlying=NIFTY
WS   /options/ws/live?underlying=NIFTY&expiry=2026-02-27
"""
import asyncio
import json
import logging
from datetime import datetime, date
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, WebSocket, WebSocketDisconnect

from app.database                   import get_pool
from app.market_data.atm_selector   import select_atm_from_straddle_legs
from app.serializers.market_data    import serialize_option_row
from app.instruments.atm_calculator import get_atm, get_underlying_price, set_atm
import app.instruments.subscription_manager as subscription_manager

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/options", tags=["Options"])


# ── Config per underlying ─────────────────────────────────────────────────

_LOT_SIZES = {
    "NIFTY": 75, "BANKNIFTY": 30, "SENSEX": 20, "MIDCPNIFTY": 120,
    "FINNIFTY": 40, "BANKEX": 15,
}
_STRIKE_INTERVALS = {
    "NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100, "MIDCPNIFTY": 25,
    "FINNIFTY": 50, "BANKEX": 100,
}


def _lot_size(u: str) -> int:
    return _LOT_SIZES.get(u.upper(), 1)

def _strike_interval(u: str) -> int:
    return _STRIKE_INTERVALS.get(u.upper(), 100)


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/live")
async def get_option_chain(
    underlying:     str   = Query(...),
    expiry:         str   = Query(..., description="YYYY-MM-DD"),
    strikes_around: int   = Query(default=15, ge=1, le=50),
):
    """
    Option chain for a given underlying + expiry.
    Returns strikes dict keyed by strike price string.
    """
    underlying = underlying.upper()
    pool       = get_pool()
    atm_raw    = get_atm(underlying)
    cached_atm: float | None = float(atm_raw) if atm_raw is not None else None
    spot_raw   = get_underlying_price(underlying)
    cached_spot: float | None = float(spot_raw) if spot_raw is not None else None

    try:
        expiry_date: date = datetime.strptime(expiry, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="expiry must be YYYY-MM-DD")

    # Lot size from instrument_master (CSV-derived), fallback to config mapping
    lot_size_csv: Optional[int] = None
    try:
        lot_row = await pool.fetchrow(
            """
            SELECT lot_size
            FROM instrument_master
            WHERE underlying = $1
              AND instrument_type IN ('OPTIDX','OPTSTK','OPTFUT')
              AND expiry_date = $2::date
              AND lot_size IS NOT NULL
            LIMIT 1
            """,
            underlying,
            expiry_date,
        )
        if lot_row and lot_row["lot_size"]:
            lot_size_csv = int(lot_row["lot_size"])
    except Exception:
        lot_size_csv = None

    # Underlying LTP from freshest available source (INDEX or nearest futures).
    # This prevents stale ATM cache from becoming the de-facto underlying price.
    ul_row = await pool.fetchrow(
        """
        SELECT ltp, updated_at
        FROM (
            SELECT md.ltp, md.updated_at
            FROM market_data md
            JOIN instrument_master im ON im.instrument_token = md.instrument_token
            WHERE im.symbol = $1
              AND im.instrument_type = 'INDEX'

            UNION ALL

            SELECT md.ltp, md.updated_at
            FROM market_data md
            JOIN instrument_master im ON im.instrument_token = md.instrument_token
            WHERE im.underlying = $1
              AND im.instrument_type IN ('FUTIDX','FUTSTK','FUTCOM')
              AND im.expiry_date >= CURRENT_DATE
        ) q
        WHERE ltp IS NOT NULL
        ORDER BY updated_at DESC NULLS LAST
        LIMIT 1
        """,
        underlying,
    )
    db_underlying_ltp = float(ul_row["ltp"]) if (ul_row and ul_row["ltp"]) else None
    # Prefer Dhan spot cached by greeks_poller; fallback to DB then ATM cache.
    underlying_ltp = cached_spot if (cached_spot is not None and cached_spot > 0) else (
        db_underlying_ltp if db_underlying_ltp is not None else (cached_atm or 0.0)
    )

    ocd_rows = await pool.fetch(
        """
        WITH ranked AS (
            SELECT
                ocd.*,
                ocd.prev_close AS ocd_prev_close,
                md.ltp,
                md.open,
                md.high,
                md.low,
                md.close,
                md.bid_depth,
                md.ask_depth,
                md.ltt,
                md.updated_at,
                im.exchange_segment,
                im.tick_size,
                im.symbol,
                ROW_NUMBER() OVER (
                    PARTITION BY ocd.strike_price, ocd.option_type
                    ORDER BY
                        COALESCE(md.updated_at, ocd.greeks_updated_at) DESC NULLS LAST,
                        ocd.greeks_updated_at DESC NULLS LAST,
                        ocd.instrument_token DESC
                ) AS rn
            FROM option_chain_data ocd
            LEFT JOIN market_data md ON md.instrument_token = ocd.instrument_token
            LEFT JOIN instrument_master im ON im.instrument_token = ocd.instrument_token
            WHERE ocd.underlying = $1 AND ocd.expiry_date = $2::date
        )
        SELECT *
        FROM ranked
        WHERE rn = 1
        ORDER BY strike_price, option_type
        """,
        underlying, expiry_date,
    )

    if not ocd_rows:
        # Return empty structure rather than 404 so UI degrades gracefully
        return {
            "underlying": underlying,
            "expiry":     expiry,
            "underlying_ltp": underlying_ltp,
            "lot_size":   lot_size_csv or _lot_size(underlying),
            "strike_interval": _strike_interval(underlying),
            "atm":        cached_atm,
            "strikes":    {},
        }

    all_strikes = sorted({float(r["strike_price"]) for r in ocd_rows})

    # ATM rule: strike with minimum (CE LTP + PE LTP).
    # Build straddle sums from current option_chain_data + market_data fallback.
    straddle_prices: dict[float, dict[str, float]] = {}
    for row in ocd_rows:
        strike = float(row["strike_price"])
        ltp_val = row["ltp"] if row["ltp"] is not None else row["ocd_prev_close"]
        if ltp_val is None:
            continue
        ltp = float(ltp_val)
        if ltp <= 0:
            continue
        opt_type = str(row["option_type"] or "").upper()
        if opt_type not in ("CE", "PE"):
            continue
        legs = straddle_prices.setdefault(strike, {})
        legs[opt_type] = ltp

    straddle_atm, _atm_meta = select_atm_from_straddle_legs(
        straddle_prices,
        spot_price=underlying_ltp if underlying_ltp > 0 else None,
        strike_step=float(_strike_interval(underlying)),
    )

    effective_atm: float | None = straddle_atm if straddle_atm is not None else cached_atm
    if effective_atm is not None and (cached_atm is None or abs(cached_atm - effective_atm) >= 0.001):
        try:
            set_atm(underlying, effective_atm, underlying_ltp if underlying_ltp > 0 else None)
        except Exception:
            pass

    if effective_atm is not None and all_strikes:
        closest = min(all_strikes, key=lambda s: abs(s - float(effective_atm)))
        idx     = all_strikes.index(closest)
        lo      = max(0, idx - strikes_around)
        hi      = min(len(all_strikes) - 1, idx + strikes_around)
        active  = set(all_strikes[lo : hi + 1])
    else:
        active = set(all_strikes)

    result: dict[str, dict] = {}
    for row in ocd_rows:
        row_data = dict(row)
        strike = float(row_data["strike_price"])
        if strike not in active:
            continue
        # Closed-market fallback: use prev_close when ltp/close are missing.
        if row_data.get("ltp") is None and row_data.get("ocd_prev_close") is not None:
            row_data["ltp"] = row_data.get("ocd_prev_close")
        if row_data.get("close") is None and row_data.get("ocd_prev_close") is not None:
            row_data["close"] = row_data.get("ocd_prev_close")
        key = str(int(strike)) if strike == int(strike) else str(strike)
        if key not in result:
            result[key] = {"strike": strike, "CE": None, "PE": None}
        opt_type = row_data["option_type"]
        inferred_segment = "BSE_FNO" if underlying in ("SENSEX", "BANKEX") else "NSE_FNO"
        result[key][opt_type] = serialize_option_row(
            tick=row_data,
            ocd=row_data,
            segment=row_data.get("exchange_segment") or inferred_segment,
        )

    # Server-driven Tier-A subscriptions (idempotent)
    # Ensures frontend never drives Dhan subscription decisions.
    try:
        tokens = [int(r["instrument_token"]) for r in ocd_rows]
        im_rows = await pool.fetch(
            "SELECT instrument_token, tier FROM instrument_master WHERE instrument_token = ANY($1::bigint[])",
            tokens,
        )
        tier_map = {int(r["instrument_token"]): (r["tier"] or "B") for r in im_rows}
        for t in tokens:
            if tier_map.get(int(t)) == "A":
                await subscription_manager.subscribe_tier_a(int(t))
    except Exception:
        # Subscription attempts are best-effort; serving cached data must still work.
        pass

    return {
        "underlying":      underlying,
        "expiry":          expiry,
        "underlying_ltp":  underlying_ltp,
        "lot_size":        lot_size_csv or _lot_size(underlying),
        "strike_interval": _strike_interval(underlying),
        "atm":             effective_atm,
        "strikes":         result,
    }


@router.get("/available/expiries")
async def get_expiries(underlying: str = Query(...)):
    """List available expiry dates for an underlying (upcoming only)."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT DISTINCT expiry_date FROM option_chain_data
        WHERE underlying = $1 AND expiry_date >= CURRENT_DATE
        ORDER BY expiry_date
        """,
        underlying.upper(),
    )
    expiries = [str(r["expiry_date"]) for r in rows]

    # Also check instrument_master for expiries (option chain data may not be loaded yet)
    if not expiries:
        rows2 = await pool.fetch(
            """
            SELECT DISTINCT expiry_date FROM instrument_master
            WHERE underlying = $1
              AND instrument_type IN ('OPTIDX','OPTSTK','OPTFUT')
              AND expiry_date >= CURRENT_DATE
            ORDER BY expiry_date
            LIMIT 12
            """,
            underlying.upper(),
        )
        expiries = [str(r["expiry_date"]) for r in rows2]

    return {"data": expiries}


@router.websocket("/ws/live")
async def option_chain_ws(
    ws:         WebSocket,
    underlying: str = Query(...),
    expiry:     str = Query(...),
):
    """
    Streams live option chain updates every second.
    Sends full chain snapshot on each tick.
    """
    await ws.accept()
    try:
        expiry_date: date = datetime.strptime(expiry, "%Y-%m-%d").date()
    except ValueError:
        await ws.close(code=1008)
        return
    try:
        while True:
            pool = get_pool()
            ocd_rows = await pool.fetch(
                """
                WITH ranked AS (
                    SELECT
                        ocd.instrument_token,
                        ocd.strike_price,
                        ocd.option_type,
                        COALESCE(md.ltp, 0) AS ltp,
                        md.updated_at,
                        ocd.greeks_updated_at,
                        ocd.iv,
                        ocd.delta,
                        ocd.theta,
                        ocd.gamma,
                        ocd.vega,
                        ROW_NUMBER() OVER (
                            PARTITION BY ocd.strike_price, ocd.option_type
                            ORDER BY
                                COALESCE(md.updated_at, ocd.greeks_updated_at) DESC NULLS LAST,
                                ocd.greeks_updated_at DESC NULLS LAST,
                                ocd.instrument_token DESC
                        ) AS rn
                    FROM option_chain_data ocd
                    LEFT JOIN market_data md ON md.instrument_token = ocd.instrument_token
                    WHERE ocd.underlying = $1 AND ocd.expiry_date = $2::date
                )
                SELECT instrument_token, strike_price, option_type, ltp, iv, delta, theta, gamma, vega
                FROM ranked
                WHERE rn = 1
                ORDER BY strike_price, option_type
                """,
                underlying.upper(), expiry_date,
            )
            strikes: dict = {}
            for row in ocd_rows:
                key      = str(float(row["strike_price"]))
                opt_type = row["option_type"]
                if key not in strikes:
                    strikes[key] = {"CE": None, "PE": None}
                strikes[key][opt_type] = {
                    "instrument_token": int(row["instrument_token"]),
                    "ltp":   float(row["ltp"]) if row["ltp"] else None,
                    "iv":    float(row["iv"])  if row["iv"]  else None,
                    "delta": float(row["delta"]) if row["delta"] else None,
                    "theta": float(row["theta"]) if row["theta"] else None,
                }
            await ws.send_text(json.dumps({
                "type":    "option_chain",
                "strikes": strikes,
            }))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning(f"Option chain WS error for {underlying}/{expiry}: {exc}")
        try:
            await ws.close()
        except Exception:
            pass
