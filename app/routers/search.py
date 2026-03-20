"""
app/routers/search.py
GET /subscriptions/search?tier=TIER_A&q=X
GET /instruments/search?q=X
GET /instruments/futures/search?q=X
GET /options/strikes/search?q=X
"""
import logging
import re
from typing import Optional

from fastapi import APIRouter, Query

from app.database import get_pool
from app.instruments.atm_calculator import get_atm
from app.market_hours import is_market_open

log    = logging.getLogger(__name__)
router = APIRouter(tags=["Search"])


def _search_tokens(q: str) -> list[str]:
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", (q or "").strip()) if len(t) >= 2]
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(token)
    return ordered


def _build_search_where(q: str, field_prefix: str = "") -> tuple[str, list[str]]:
    prefix = f"{field_prefix}." if field_prefix else ""
    fields = [
        f"{prefix}symbol",
        f"COALESCE({prefix}underlying, '')",
        f"COALESCE({prefix}display_name, '')",
        f"COALESCE({prefix}trading_symbol, '')",
    ]
    tokens = _search_tokens(q)
    args: list[str] = []

    if not tokens:
        args.append(f"%{q}%")
        like_ref = f"${len(args)}"
        clause = " OR ".join(f"{field} ILIKE {like_ref}" for field in fields)
        return f"({clause})", args

    groups: list[str] = []
    for token in tokens:
        args.append(f"%{token}%")
        like_ref = f"${len(args)}"
        groups.append("(" + " OR ".join(f"{field} ILIKE {like_ref}" for field in fields) + ")")
    return " AND ".join(groups), args


def _fmt_instrument(r) -> dict:
    d = dict(r)
    d["instrument_token"] = int(d.get("instrument_token") or 0)
    # Symbol now contains DISPLAY_NAME (full company name) for searchability.
    # SYMBOL_NAME (the ticker) is stored in display_name field for reference.
    # Frontend compatibility: many components expect token/security_id fields.
    d["token"] = d["instrument_token"]
    d["security_id"] = str(d["instrument_token"]) if d["instrument_token"] else ""
    # Optional price fields (when joined).
    if "ltp" in d:
        d["ltp"] = float(d["ltp"]) if d.get("ltp") is not None else None
    if "close" in d:
        d["close"] = float(d["close"]) if d.get("close") is not None else None
    if d.get("ltp") is not None and d.get("close") not in (None, 0):
        try:
            d["change_pct"] = round((float(d["ltp"]) - float(d["close"])) / float(d["close"]) * 100, 2)
        except Exception:
            d["change_pct"] = None
    if d.get("expiry_date"):
        d["expiry_date"] = str(d["expiry_date"])
    return d


async def _search(q: str, extra_filter: str = "", limit: int = 50) -> list:
    pool = get_pool()
    where_clause, where_args = _build_search_where(q)
    q_exact = (q or "").strip().upper()
    exact_ref = f"${len(where_args) + 1}"
    sql  = f"""
     SELECT instrument_token, symbol, exchange_segment,
         display_name, trading_symbol,
         underlying, instrument_type, expiry_date, strike_price, option_type,
         md.ltp, md.close
        FROM instrument_master im
        LEFT JOIN market_data md ON md.instrument_token = im.instrument_token
     WHERE {where_clause}
        {extra_filter}
        AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)
        ORDER BY
            -- Prioritize EQUITY and INDEX instruments first
            CASE
                WHEN instrument_type IN ('EQUITY', 'INDEX') THEN 0
                ELSE 1
            END,
            -- Then match quality
            CASE
                WHEN upper(symbol) = {exact_ref} OR upper(COALESCE(underlying, '')) = {exact_ref} THEN 0
                WHEN upper(COALESCE(display_name, '')) = {exact_ref} OR upper(COALESCE(trading_symbol, '')) = {exact_ref} THEN 0
                WHEN upper(symbol) LIKE ({exact_ref} || '%') THEN 1
                WHEN upper(COALESCE(underlying, '')) LIKE ({exact_ref} || '%') THEN 2
                WHEN upper(COALESCE(display_name, '')) LIKE ({exact_ref} || '%') THEN 2
                WHEN upper(COALESCE(trading_symbol, '')) LIKE ({exact_ref} || '%') THEN 2
                WHEN upper(symbol) LIKE ('%' || {exact_ref} || '%') THEN 3
                WHEN upper(COALESCE(underlying, '')) LIKE ('%' || {exact_ref} || '%') THEN 4
                WHEN upper(COALESCE(display_name, '')) LIKE ('%' || {exact_ref} || '%') THEN 4
                WHEN upper(COALESCE(trading_symbol, '')) LIKE ('%' || {exact_ref} || '%') THEN 4
                ELSE 5
            END,
            symbol
        LIMIT {limit}
    """
    try:
        rows = await pool.fetch(sql, *where_args, q_exact)
        return [_fmt_instrument(r) for r in rows]
    except Exception as exc:
        msg = str(exc)
        log.error("Instrument search failed: %s", msg)
        # Fallback: retry without market_data join for any failure.
        fallback_where, fallback_args = _build_search_where(q)
        fallback_exact_ref = f"${len(fallback_args) + 1}"
        fallback_sql = f"""
         SELECT instrument_token, symbol, exchange_segment,
             display_name, trading_symbol,
             underlying, instrument_type, expiry_date, strike_price, option_type,
             NULL::numeric AS ltp, NULL::numeric AS close
            FROM instrument_master
         WHERE {fallback_where}
            {extra_filter}
            AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)
            ORDER BY
                -- Prioritize EQUITY and INDEX instruments first
                CASE
                    WHEN instrument_type IN ('EQUITY', 'INDEX') THEN 0
                    ELSE 1
                END,
                -- Then match quality
                CASE
                    WHEN upper(symbol) = {fallback_exact_ref} OR upper(COALESCE(underlying, '')) = {fallback_exact_ref} THEN 0
                    WHEN upper(COALESCE(display_name, '')) = {fallback_exact_ref} OR upper(COALESCE(trading_symbol, '')) = {fallback_exact_ref} THEN 0
                    WHEN upper(symbol) LIKE ({fallback_exact_ref} || '%') THEN 1
                    WHEN upper(COALESCE(underlying, '')) LIKE ({fallback_exact_ref} || '%') THEN 2
                    WHEN upper(COALESCE(display_name, '')) LIKE ({fallback_exact_ref} || '%') THEN 2
                    WHEN upper(COALESCE(trading_symbol, '')) LIKE ({fallback_exact_ref} || '%') THEN 2
                    WHEN upper(symbol) LIKE ('%' || {fallback_exact_ref} || '%') THEN 3
                    WHEN upper(COALESCE(underlying, '')) LIKE ('%' || {fallback_exact_ref} || '%') THEN 4
                    WHEN upper(COALESCE(display_name, '')) LIKE ('%' || {fallback_exact_ref} || '%') THEN 4
                    WHEN upper(COALESCE(trading_symbol, '')) LIKE ('%' || {fallback_exact_ref} || '%') THEN 4
                    ELSE 5
                END,
                symbol
            LIMIT {limit}
        """
        try:
            rows = await pool.fetch(fallback_sql, *fallback_args, q_exact)
            return [_fmt_instrument(r) for r in rows]
        except Exception as fallback_exc:
            log.error("Instrument search fallback failed: %s", fallback_exc)
            # Final fallback: schema-safe query (does not require optional columns).
            minimal_where, minimal_args = _build_search_where(q)
            minimal_exact_ref = f"${len(minimal_args) + 1}"
            minimal_sql = f"""
             SELECT instrument_token, symbol, exchange_segment,
                 NULL::text AS display_name, NULL::text AS trading_symbol,
                 underlying, instrument_type, expiry_date, strike_price, option_type,
                 NULL::numeric AS ltp, NULL::numeric AS close
                FROM instrument_master
             WHERE {minimal_where}
                {extra_filter}
                AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)
                ORDER BY
                    -- Prioritize EQUITY and INDEX instruments first
                    CASE
                        WHEN instrument_type IN ('EQUITY', 'INDEX') THEN 0
                        ELSE 1
                    END,
                    -- Then match quality
                    CASE
                        WHEN upper(symbol) = {minimal_exact_ref} OR upper(COALESCE(underlying, '')) = {minimal_exact_ref} THEN 0
                        WHEN upper(symbol) LIKE ({minimal_exact_ref} || '%') THEN 1
                        WHEN upper(COALESCE(underlying, '')) LIKE ({minimal_exact_ref} || '%') THEN 2
                        WHEN upper(symbol) LIKE ('%' || {minimal_exact_ref} || '%') THEN 3
                        WHEN upper(COALESCE(underlying, '')) LIKE ('%' || {minimal_exact_ref} || '%') THEN 4
                        ELSE 5
                    END,
                    symbol
                LIMIT {limit}
            """
            try:
                rows = await pool.fetch(minimal_sql, *minimal_args, q_exact)
                return [_fmt_instrument(r) for r in rows]
            except Exception as minimal_exc:
                log.error("Instrument search minimal fallback failed: %s", minimal_exc)
                return []


# ── Subscriptions / tiers ─────────────────────────────────────────────────

@router.get("/subscriptions/search")
async def subscriptions_search(
    q:    Optional[str] = Query(default="", alias="q"),
    tier: Optional[str] = Query(default=None),
):
    """
    Search instruments filtered by subscription tier.
    tier values: TIER_A, TIER_B, TIER_C, or None for all.
    """
    pool = get_pool()

    # Normalise tier → 'A', 'B', 'C'
    tier_letter: Optional[str] = None
    if tier:
        tier_letter = tier.replace("TIER_", "").strip().upper()

    try:
        if tier_letter:
            sql = """
                 SELECT im.instrument_token, im.symbol, im.exchange_segment,
                     im.display_name, im.trading_symbol,
                     im.underlying, im.instrument_type, im.expiry_date, im.strike_price, im.option_type,
                     md.ltp, md.close
                FROM instrument_master im
                LEFT JOIN market_data md ON md.instrument_token = im.instrument_token
                 WHERE (
                        im.symbol ILIKE $1
                     OR im.underlying ILIKE $1
                     OR COALESCE(im.display_name, '') ILIKE $1
                     OR COALESCE(im.trading_symbol, '') ILIKE $1
                 )
                  AND im.tier = $2
                  AND (im.expiry_date IS NULL OR im.expiry_date >= CURRENT_DATE)
                ORDER BY
                    -- Prioritize EQUITY and INDEX instruments first
                    CASE
                        WHEN im.instrument_type IN ('EQUITY', 'INDEX') THEN 0
                        ELSE 1
                    END,
                    -- Then match quality
                    CASE
                        WHEN upper(im.symbol) = $3 OR upper(COALESCE(im.underlying, '')) = $3 THEN 0
                        WHEN upper(COALESCE(im.display_name, '')) = $3 OR upper(COALESCE(im.trading_symbol, '')) = $3 THEN 0
                        WHEN upper(im.symbol) LIKE ($3 || '%') THEN 1
                        WHEN upper(COALESCE(im.underlying, '')) LIKE ($3 || '%') THEN 2
                        WHEN upper(COALESCE(im.display_name, '')) LIKE ($3 || '%') THEN 2
                        WHEN upper(COALESCE(im.trading_symbol, '')) LIKE ($3 || '%') THEN 2
                        WHEN upper(im.symbol) LIKE ('%' || $3 || '%') THEN 3
                        WHEN upper(COALESCE(im.underlying, '')) LIKE ('%' || $3 || '%') THEN 4
                        WHEN upper(COALESCE(im.display_name, '')) LIKE ('%' || $3 || '%') THEN 4
                        WHEN upper(COALESCE(im.trading_symbol, '')) LIKE ('%' || $3 || '%') THEN 4
                        ELSE 5
                    END,
                    im.symbol
                LIMIT 100
            """
            rows = await pool.fetch(sql, f"%{q}%", tier_letter, (q or "").strip().upper())
        else:
            rows = await pool.fetch(
                """
                 SELECT im.instrument_token, im.symbol, im.exchange_segment,
                     im.display_name, im.trading_symbol,
                     im.underlying, im.instrument_type, im.expiry_date, im.strike_price, im.option_type,
                     md.ltp, md.close
                FROM instrument_master im
                LEFT JOIN market_data md ON md.instrument_token = im.instrument_token
                 WHERE (
                        im.symbol ILIKE $1
                     OR im.underlying ILIKE $1
                     OR COALESCE(im.display_name, '') ILIKE $1
                     OR COALESCE(im.trading_symbol, '') ILIKE $1
                 )
                  AND (im.expiry_date IS NULL OR im.expiry_date >= CURRENT_DATE)
                ORDER BY
                    -- Prioritize EQUITY and INDEX instruments first
                    CASE
                        WHEN instrument_type IN ('EQUITY', 'INDEX') THEN 0
                        ELSE 1
                    END,
                    -- Then match quality
                    CASE
                        WHEN upper(symbol) = $2 OR upper(COALESCE(underlying, '')) = $2 THEN 0
                        WHEN upper(COALESCE(display_name, '')) = $2 OR upper(COALESCE(trading_symbol, '')) = $2 THEN 0
                        WHEN upper(symbol) LIKE ($2 || '%') THEN 1
                        WHEN upper(COALESCE(underlying, '')) LIKE ($2 || '%') THEN 2
                        WHEN upper(COALESCE(display_name, '')) LIKE ($2 || '%') THEN 2
                        WHEN upper(COALESCE(trading_symbol, '')) LIKE ($2 || '%') THEN 2
                        WHEN upper(symbol) LIKE ('%' || $2 || '%') THEN 3
                        WHEN upper(COALESCE(underlying, '')) LIKE ('%' || $2 || '%') THEN 4
                        WHEN upper(COALESCE(display_name, '')) LIKE ('%' || $2 || '%') THEN 4
                        WHEN upper(COALESCE(trading_symbol, '')) LIKE ('%' || $2 || '%') THEN 4
                        ELSE 5
                    END,
                    symbol
                LIMIT 100
                """,
                f"%{q}%",
                (q or "").strip().upper(),
            )
    except Exception as exc:
        log.error("Subscription search failed: %s", exc)
        if tier_letter:
            rows = await pool.fetch(
                """
                 SELECT im.instrument_token, im.symbol, im.exchange_segment,
                     NULL::text AS display_name, NULL::text AS trading_symbol,
                     im.underlying, im.instrument_type, im.expiry_date, im.strike_price, im.option_type,
                     NULL::numeric AS ltp, NULL::numeric AS close
                FROM instrument_master im
                 WHERE (
                        im.symbol ILIKE $1
                     OR COALESCE(im.underlying, '') ILIKE $1
                 )
                  AND im.tier = $2
                  AND (im.expiry_date IS NULL OR im.expiry_date >= CURRENT_DATE)
                ORDER BY
                    -- Prioritize EQUITY and INDEX instruments first
                    CASE
                        WHEN im.instrument_type IN ('EQUITY', 'INDEX') THEN 0
                        ELSE 1
                    END,
                    -- Then match quality
                    CASE
                        WHEN upper(im.symbol) = $3 OR upper(COALESCE(im.underlying, '')) = $3 THEN 0
                        WHEN upper(im.symbol) LIKE ($3 || '%') THEN 1
                        WHEN upper(COALESCE(im.underlying, '')) LIKE ($3 || '%') THEN 2
                        WHEN upper(im.symbol) LIKE ('%' || $3 || '%') THEN 3
                        WHEN upper(COALESCE(im.underlying, '')) LIKE ('%' || $3 || '%') THEN 4
                        ELSE 5
                    END,
                    im.symbol
                LIMIT 100
                """,
                f"%{q}%",
                tier_letter,
                (q or "").strip().upper(),
            )
        else:
            rows = await pool.fetch(
                """
                 SELECT im.instrument_token, im.symbol, im.exchange_segment,
                     NULL::text AS display_name, NULL::text AS trading_symbol,
                     im.underlying, im.instrument_type, im.expiry_date, im.strike_price, im.option_type,
                     NULL::numeric AS ltp, NULL::numeric AS close
                FROM instrument_master im
                 WHERE (
                        im.symbol ILIKE $1
                     OR COALESCE(im.underlying, '') ILIKE $1
                 )
                  AND (im.expiry_date IS NULL OR im.expiry_date >= CURRENT_DATE)
                ORDER BY
                    -- Prioritize EQUITY and INDEX instruments first
                    CASE
                        WHEN im.instrument_type IN ('EQUITY', 'INDEX') THEN 0
                        ELSE 1
                    END,
                    -- Then match quality
                    CASE
                        WHEN upper(im.symbol) = $2 OR upper(COALESCE(im.underlying, '')) = $2 THEN 0
                        WHEN upper(im.symbol) LIKE ($2 || '%') THEN 1
                        WHEN upper(COALESCE(im.underlying, '')) LIKE ($2 || '%') THEN 2
                        WHEN upper(im.symbol) LIKE ('%' || $2 || '%') THEN 3
                        WHEN upper(COALESCE(im.underlying, '')) LIKE ('%' || $2 || '%') THEN 4
                        ELSE 5
                    END,
                    im.symbol
                LIMIT 100
                """,
                f"%{q}%",
                (q or "").strip().upper(),
            )

    return {"data": [_fmt_instrument(r) for r in rows]}


# ── General instrument search ──────────────────────────────────────────────

@router.get("/instruments/search")
async def instruments_search(q: str = Query(default=""), limit: int = Query(default=50)):
    return await _search(q, limit=limit)


@router.get("/instruments/futures/search")
async def futures_search(q: str = Query(default="")):
    return {
        "data": await _search(
            q,
            extra_filter="AND instrument_type IN ('FUTIDX','FUTSTK','FUTFUT','FUTCOM')",
        )
    }


@router.get("/options/strikes/search")
async def option_strikes_search(
    q:          str           = Query(default=""),
    underlying: Optional[str] = Query(default=None),
    expiry:     Optional[str] = Query(default=None),
):
    pool = get_pool()

    q_raw = (q or "").strip()
    q_up = q_raw.upper()

    # Tokenize query so "NIFTY 25550" matches symbols like "NIFTY-Feb2026-25550-CE".
    raw_tokens = re.findall(r"[A-Z]+|\d+(?:\.\d+)?", q_up)
    tokens = [
        ("PE" if t == "PUT" else "CE" if t == "CALL" else t)
        for t in raw_tokens
    ]
    like_pattern = f"%{'%'.join(tokens)}%" if tokens else f"%{q_raw}%"

    strike: Optional[float] = None
    opt_type: Optional[str] = None
    for t in tokens:
        if t in ("CE", "PE"):
            opt_type = t
        elif strike is None and re.fullmatch(r"\d+(?:\.\d+)?", t) and len(t.split(".")[0]) >= 3:
            try:
                strike = float(t)
            except Exception:
                strike = None

    # If user typed an underlying as an alpha token and didn't pass underlying=,
    # constrain to that exact underlying when it exists.
    if not underlying:
        alpha = next((t for t in tokens if re.fullmatch(r"[A-Z]+", t) and t not in ("CE", "PE")), "")
        if alpha:
            exists = await pool.fetchval(
                """
                SELECT 1
                FROM instrument_master
                                WHERE underlying = $1
                                    AND instrument_type IN ('OPTIDX','OPTSTK','OPTFUT','OPTCOM')
                LIMIT 1
                """,
                alpha,
            )
            if exists:
                underlying = alpha

    has_structured = any([underlying, expiry, strike is not None, opt_type])

    def _build_where_and_args(include_strike: bool = True):
        parts = ["instrument_type IN ('OPTIDX','OPTSTK','OPTFUT','OPTCOM')", "expiry_date >= CURRENT_DATE"]
        args: list = []

        # For structured option queries (underlying/strike/type/expiry parsed),
        # avoid forcing a strict full-string LIKE that can accidentally exclude valid rows.
        if not has_structured:
            parts.append("(symbol ILIKE $1 OR underlying ILIKE $1)")
            args.append(like_pattern)

        if underlying:
            parts.append(f"underlying = ${len(args)+1}")
            args.append(underlying.upper())
        if expiry:
            parts.append(f"expiry_date = ${len(args)+1}::date")
            args.append(expiry)
        if include_strike and strike is not None:
            parts.append(f"strike_price = ${len(args)+1}::numeric")
            args.append(strike)
        if opt_type:
            parts.append(f"option_type = ${len(args)+1}")
            args.append(opt_type)
        return " AND ".join(parts), args

    atm: Optional[float] = None
    if underlying:
        try:
            atm_raw = get_atm(underlying.upper())
            atm = float(atm_raw) if atm_raw is not None else None
        except Exception:
            atm = None

    # If cached ATM is missing (common when INDEX ticks aren't present),
    # derive a sensible centre from the nearest futures (close when market is closed).
    if underlying and atm is None:
        row = await pool.fetchrow(
            """
                        SELECT md.ltp, md.close, im.exchange_segment
            FROM instrument_master im
            JOIN market_data md ON md.instrument_token = im.instrument_token
            WHERE im.underlying = $1
              AND im.instrument_type IN ('FUTIDX','FUTSTK','FUTCOM')
              AND (md.ltp IS NOT NULL OR md.close IS NOT NULL)
            ORDER BY
              (im.expiry_date >= CURRENT_DATE) DESC,
              im.expiry_date NULLS LAST,
              md.updated_at DESC
            LIMIT 1
            """,
            underlying.upper(),
        )
        if row:
            seg = (row.get("exchange_segment") or "NSE_FNO")
            market_active = is_market_open(seg, underlying.upper())
            val = row["ltp"] if market_active else (row["close"] or row["ltp"])
            if val is not None:
                try:
                    atm = float(val)
                except Exception:
                    atm = None

    where_str, args = _build_where_and_args(include_strike=True)
    order_centre = strike if strike is not None else atm
    if order_centre is not None:
        order_by = f"ORDER BY ABS(strike_price - ${len(args)+1}::numeric), expiry_date, option_type"
        args.append(order_centre)
    else:
        order_by = "ORDER BY expiry_date, strike_price, option_type"

    sql = f"""
         SELECT instrument_token, symbol, exchange_segment,
             underlying, instrument_type, expiry_date, strike_price, option_type
        FROM instrument_master
            WHERE {where_str}
        {order_by}
        LIMIT 200
    """
    rows = await pool.fetch(sql, *args)

    # If exact strike returned nothing, fallback to nearest available strikes
    # (same underlying/expiry/type constraints) so users still get actionable results.
    if not rows and strike is not None:
        fb_where, fb_args = _build_where_and_args(include_strike=False)
        fb_order_by = f"ORDER BY ABS(strike_price - ${len(fb_args)+1}::numeric), expiry_date, option_type"
        fb_args.append(strike)
        fb_sql = f"""
             SELECT instrument_token, symbol, exchange_segment,
                 underlying, instrument_type, expiry_date, strike_price, option_type
            FROM instrument_master
                WHERE {fb_where}
            {fb_order_by}
            LIMIT 200
        """
        rows = await pool.fetch(fb_sql, *fb_args)

    return {"data": [_fmt_instrument(r) for r in rows]}
