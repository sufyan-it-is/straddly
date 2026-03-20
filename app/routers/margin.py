"""
app/routers/margin.py
POST /margin/calculate   {user_id, symbol, security_id, exchange_segment,
                          transaction_type, quantity, order_type, product_type, price}
GET  /margin/account?user_id=X
GET  /margin/span-data?symbol=NIFTY   (admin/debug — returns raw SPAN cache entry)

Margin calculation uses real daily NSE SPAN® + Exposure Limit data downloaded
from nsearchives.nseindia.com every morning at 08:45 IST.

Formula:
    Option buyer  → total = premium           (= ltp × quantity)
    Option seller → total = SPAN + Exposure
    Futures       → total = SPAN + Exposure

Where:
    SPAN margin  = priceScan (from SPAN® file) × quantity
    Exposure     = ref_price × quantity × ELM% / 100  (from AEL file)
"""
import logging
from typing import Optional

from fastapi  import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.database           import get_pool
from app.dependencies       import CurrentUser, get_current_user
from app.market_data.rate_limiter import dhan_client
from app.margin.nse_margin_data import (
    calculate_margin as _nse_calculate_margin,
    get_span_data,
    get_store,
    download_and_refresh,
)

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/margin", tags=["Margin"])


def _uid(request: Request, user_id_param, current_user: Optional[CurrentUser] = None) -> str:
    if user_id_param:
        return str(user_id_param)
    hdr = request.headers.get("X-USER")
    if hdr:
        return hdr
    if current_user:
        return str(current_user.id)
    raise HTTPException(status_code=401, detail="Authentication required")


def _detect_instrument(
    symbol: str,
    exchange_segment: str,
    instrument_type: Optional[str] = None,
    option_type: Optional[str] = None,
) -> tuple[bool, bool, bool]:
    """Detect (is_option, is_futures, is_commodity) from symbol + segment + metadata."""
    sym = (symbol or "").upper()
    seg = (exchange_segment or "").upper()
    inst = (instrument_type or "").upper()
    opt = (option_type or "").upper()

    is_option = False
    if opt in {"CE", "PE"}:
        is_option = True
    elif inst.startswith("OPT"):
        is_option = True
    elif "OPT" in seg:
        is_option = True
    else:
        if sym.endswith(("CE", "PE", "CALL", "PUT")):
            is_option = True
        elif " CE " in sym or " PE " in sym or " CALL " in sym or " PUT " in sym:
            is_option = True
        else:
            parts = sym.replace("-", " ").replace("/", " ").split()
            if parts and parts[-1] in {"CE", "PE", "CALL", "PUT"}:
                is_option = True

    is_commodity = (
        "MCX" in seg
        or "COM" in seg
        or inst.endswith("COM")
        or inst.startswith("FUTCOM")
        or inst.startswith("OPTFUT")
    )
    is_futures = (
        not is_option
        and (
            inst.startswith("FUT")
            or "FUT" in seg
            or seg in ("NSE_FNO", "BSE_FNO", "MCX_FO", "NSE_COM")
        )
    )

    return is_option, is_futures, is_commodity


def _extract_underlying(symbol: str) -> str:
    """
    Extract underlying symbol from a full option/futures symbol.
    E.g.  "NIFTY24FEB25000CE" → "NIFTY"
          "BANKNIFTY"         → "BANKNIFTY"
          "RELIANCE"          → "RELIANCE"
    """
    sym = (symbol or "").upper().strip()
    # Strip trailing CE/PE
    for suffix in ("CE", "PE"):
        if sym.endswith(suffix):
            sym = sym[:-2]
            break
    # Strip trailing digit-only expiry/strike (e.g. 24FEB25000 → just letters)
    import re
    # Try to match known index/stock prefixes
    m = re.match(r"^([A-Z&]+)", sym)
    if m:
        return m.group(1)
    return sym


def _map_span_underlying(underlying: str, exchange_segment: str) -> str:
    """Map exchange-specific index underlyings to available SPAN symbols."""
    und = (underlying or "").upper().strip()
    seg = (exchange_segment or "").upper().strip()
    if "BSE" in seg:
        if und == "SENSEX":
            return "NIFTY"
        if und == "BANKEX":
            return "BANKNIFTY"
    return und


async def _calculate_mcx_margin(
    pool,
    symbol: str,
    quantity: int,
    ltp: float,
    transaction_type: str,
    is_option: bool,
) -> dict:
    """Calculate MCX margin from mcx_span_margin_cache (SPAN + ELM)."""
    qty = int(quantity or 0)
    if qty <= 0:
        return {
            "span_margin": 0.0,
            "exposure_margin": 0.0,
            "premium": 0.0,
            "total_margin": 0.0,
            "elm_pct": 0.0,
            "span_source": "mcx_cache",
            "data_as_of": None,
            "error": None,
        }

    tx = (transaction_type or "BUY").upper()
    if is_option and tx == "BUY":
        premium = round(float(ltp or 0.0) * qty, 2)
        return {
            "span_margin": 0.0,
            "exposure_margin": 0.0,
            "premium": premium,
            "total_margin": premium,
            "elm_pct": 0.0,
            "span_source": "premium",
            "data_as_of": None,
            "error": None,
        }

    sym = (symbol or "").upper().strip()
    und = _extract_underlying(sym)
    row = await pool.fetchrow(
        """
        SELECT symbol, ref_price, price_scan, contract_value_factor, elm_pct, downloaded_at
        FROM mcx_span_margin_cache
        WHERE is_latest = true
          AND symbol IN ($1, $2)
        ORDER BY CASE WHEN symbol = $1 THEN 0 ELSE 1 END
        LIMIT 1
        """,
        sym,
        und,
    )
    if not row:
        return {
            "span_margin": None,
            "exposure_margin": None,
            "premium": None,
            "total_margin": None,
            "elm_pct": None,
            "span_source": "mcx_cache",
            "data_as_of": None,
            "error": f"MCX SPAN data not available for {und or sym}",
        }

    cvf = float(row["contract_value_factor"] or 1.0)
    if cvf <= 0:
        cvf = 1.0
    ref_price = float(row["ref_price"] or ltp or 0.0)
    price_scan = float(row["price_scan"] or 0.0)
    elm_pct = float(row["elm_pct"] or 0.0)

    span_margin = price_scan * qty / cvf
    exposure_margin = (ref_price * qty * (elm_pct / 100.0)) / cvf
    total_margin = span_margin + exposure_margin

    return {
        "span_margin": round(span_margin, 2),
        "exposure_margin": round(exposure_margin, 2),
        "premium": 0.0,
        "total_margin": round(total_margin, 2),
        "elm_pct": round(elm_pct, 4),
        "span_source": "mcx_cache",
        "data_as_of": row["downloaded_at"].isoformat() if row["downloaded_at"] else None,
        "error": None,
    }


class MarginCalcRequest(BaseModel):
    user_id:          Optional[str]   = None
    symbol:           Optional[str]   = None
    security_id:      Optional[str]   = None
    exchange_segment: Optional[str]   = ""
    transaction_type: Optional[str]   = "BUY"
    quantity:         Optional[int]   = 1
    order_type:       Optional[str]   = "MARKET"
    product_type:     Optional[str]   = "INTRADAY"
    price:            Optional[float] = 0.0


def _extract_quote_price(payload: object, instrument_token: int) -> Optional[float]:
    """Best-effort parser for quote payloads returned by real/mock marketfeed APIs."""
    token_keys = {"securityid", "security_id", "instrument_token", "instrumenttoken", "token"}
    price_keys = {
        "ltp", "lastprice", "last_price", "lasttradedprice", "last_traded_price",
        "close", "price",
    }

    def _to_float(v) -> Optional[float]:
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    stack = [payload]
    fallback_price: Optional[float] = None
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            lowered = {str(k).lower(): v for k, v in cur.items()}
            token_match = False
            for tk in token_keys:
                tv = lowered.get(tk)
                if tv is None:
                    continue
                try:
                    if int(str(tv)) == int(instrument_token):
                        token_match = True
                        break
                except (TypeError, ValueError):
                    continue

            for pk in price_keys:
                if pk in lowered:
                    price_val = _to_float(lowered.get(pk))
                    if price_val is not None:
                        if token_match:
                            return price_val
                        if fallback_price is None:
                            fallback_price = price_val

            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            for item in cur:
                if isinstance(item, (dict, list)):
                    stack.append(item)

    return fallback_price


async def _fetch_quote_price_from_feed(instrument_token: int, exchange_segment: str) -> Optional[float]:
    """Fetch latest price via marketfeed quote API when cache is not populated yet."""
    segment = (exchange_segment or "NSE_EQ").upper()
    req_body = {
        "ExchangeSegment": segment,
        "SecurityId": str(instrument_token),
        "tokens": [instrument_token],
        "instrument_tokens": [instrument_token],
        segment: [str(instrument_token)],
    }
    try:
        resp = await dhan_client.post("/marketfeed/quote", json=req_body)
        if resp.status_code >= 400:
            return None
        return _extract_quote_price(resp.json(), instrument_token)
    except Exception:
        return None


@router.post("/calculate")
async def calculate_margin_endpoint(body: MarginCalcRequest, request: Request):
    pool  = get_pool()
    price = body.price or 0.0
    sym   = (body.symbol or "").strip()
    seg   = (body.exchange_segment or "").strip()
    inst_type = None
    opt_type = None

    instrument_token: Optional[int] = None
    if body.security_id is not None:
        sid = str(body.security_id).strip()
        if sid.isdigit():
            instrument_token = int(sid)

    # Normalize exchange segment and symbol using instrument_master when possible.
    if instrument_token:
        im = await pool.fetchrow(
            """
            SELECT symbol, exchange_segment, instrument_type, option_type
            FROM instrument_master
            WHERE instrument_token=$1
            """,
            instrument_token,
        )
        if im:
            if not sym:
                sym = (im.get("symbol") or "").strip()
            # Prefer database exchange_segment for accurate instrument detection
            if (not seg) or seg.upper() in {"NSE", "BSE", "MCX", "NSE_FNO"}:
                seg = (im.get("exchange_segment") or "").strip()
            inst_type = im.get("instrument_type")
            opt_type = im.get("option_type")

    # ── Resolve LTP if price not supplied ────────────────────────────────────
    if price == 0.0 and (instrument_token or sym):
        # IMPORTANT:
        # - The frontend sends `security_id` but it is actually the instrument_token.
        # - instrument_master does NOT have a `security_id` column.
        # - Search in both symbol (DISPLAY_NAME) and underlying (SYMBOL/ticker) columns
        #   because symbol now contains full company names for searchability.
        if instrument_token is None and sym:
            instrument_token = await pool.fetchval(
                "SELECT instrument_token FROM instrument_master WHERE symbol ILIKE $1 OR underlying ILIKE $1 LIMIT 1",
                sym,
            )

        if instrument_token:
            ltp_row = await pool.fetchrow(
                "SELECT ltp FROM market_data WHERE instrument_token=$1",
                instrument_token,
            )
            if ltp_row and ltp_row["ltp"]:
                price = float(ltp_row["ltp"])

    # Fallback to quote API when DB cache is empty (common for newly-selected Tier-A tokens).
    if price == 0.0 and instrument_token:
        quote_price = await _fetch_quote_price_from_feed(instrument_token, seg)
        if quote_price is not None:
            price = float(quote_price)

    if price == 0.0:
        price = 0.0   # margin will be 0 — caller should supply price

    qty      = int(body.quantity or 1)
    tx_type  = (body.transaction_type or "BUY").upper()

    is_option, is_futures, is_commodity = _detect_instrument(
        sym,
        seg,
        instrument_type=inst_type,
        option_type=opt_type,
    )
    is_equity = not is_option and not is_futures and not is_commodity

    # ── Cash equity margin (qty × price) ─────────────────────────────────────
    if is_equity:
        cash_required = round(price * qty, 2)
        return {
            "data": {
                "required_margin": cash_required,
                "span_margin":     0.0,
                "exposure_margin": 0.0,
                "premium":         cash_required,
                "elm_pct":         0.0,
                "price_used":      round(price, 2),
                "quantity":        qty,
                "underlying":      sym or None,
                "span_source":     "cash",
                "data_as_of":      None,
                "error":           None,
            }
        }

    # ── Option BUY margin (premium only) ────────────────────────────────────
    if is_option and tx_type == "BUY":
        premium = round(price * qty, 2)
        return {
            "data": {
                "required_margin": premium,
                "span_margin":     0.0,
                "exposure_margin": 0.0,
                "premium":         premium,
                "elm_pct":         0.0,
                "price_used":      round(price, 2),
                "quantity":        qty,
                "underlying":      _extract_underlying(sym),
                "span_source":     "premium",
                "data_as_of":      None,
                "error":           None,
            }
        }

    # For SPAN lookup, use the underlying symbol (strip expiry/strike)
    underlying = _extract_underlying(sym)
    span_underlying = _map_span_underlying(underlying, seg)

    # ── Margin calculation: MCX uses MCX cache, others keep existing path ───
    if "MCX" in seg.upper():
        breakdown = await _calculate_mcx_margin(
            pool=pool,
            symbol=span_underlying,
            quantity=qty,
            ltp=price,
            transaction_type=tx_type,
            is_option=is_option,
        )
    else:
        breakdown = _nse_calculate_margin(
            symbol=span_underlying,
            transaction_type=tx_type,
            quantity=qty,
            ltp=price,
            is_option=is_option,
            is_futures=is_futures,
            is_commodity=is_commodity,
        )

    # calculate_margin() can return an error dict (missing keys like span_source)
    # when SPAN/ELM data is unavailable. Never 500 here — the UI treats failures
    # as 0 and should not break the modal.
    required_margin = breakdown.get("total_margin")
    if required_margin is None:
        required_margin = 0.0

    return {
        "data": {
            "required_margin": required_margin,
            "span_margin":     breakdown.get("span_margin"),
            "exposure_margin": breakdown.get("exposure_margin"),
            "premium":         breakdown.get("premium"),
            "elm_pct":         breakdown.get("elm_pct"),
            "price_used":      round(price, 2),
            "quantity":        qty,
            "underlying":      underlying,
            "span_source":     breakdown.get("span_source"),
            "data_as_of":      breakdown.get("data_as_of"),
            "error":           breakdown.get("error"),
        }
    }


@router.get("/account")
async def margin_account(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    user_id: Optional[str] = Query(None),
):
    uid  = _uid(request, user_id, current_user)
    pool = get_pool()

    # ────────────────────────────────────────────────────────────────────────
    # FIX: Use actual SPAN + Exposure margins instead of percentage approximations
    # This ensures consistency with order placement margin checks
    # ALSO: Include pending orders margin to prevent over-leveraging
    # ────────────────────────────────────────────────────────────────────────
    row = await pool.fetchrow(
        """
        SELECT
            COALESCE(pa.balance, 0)         AS wallet_balance,
            COALESCE(pa.margin_allotted, 0) AS margin_allotted,
            COALESCE(SUM(
                calculate_position_margin(
                    pp.instrument_token,
                    pp.symbol,
                    pp.exchange_segment,
                    pp.quantity,
                    pp.product_type
                )
            ) FILTER (WHERE pp.status='OPEN' AND pp.quantity != 0), 0) AS positions_margin,
            COALESCE(calculate_pending_orders_margin(pa.user_id), 0) AS pending_orders_margin
        FROM paper_accounts pa
        LEFT JOIN paper_positions pp ON pp.user_id = pa.user_id
        WHERE pa.user_id = $1::uuid
        GROUP BY pa.user_id, pa.balance, pa.margin_allotted
        """,
        uid,
    )
    if not row:
        # Unknown users: margin is explicitly not allotted
        return {
            "data": {
                "available_margin": 0.0,
                "used_margin": 0.0,
                "total_balance": 0.0,
                "allotted_margin": 0.0,
                "wallet_balance": 0.0,
            }
        }

    wallet_balance  = float(row["wallet_balance"]  or 0)
    margin_allotted = float(row["margin_allotted"] or 0)
    positions_margin = float(row["positions_margin"] or 0)
    pending_orders_margin = float(row["pending_orders_margin"] or 0)
    
    # Total used margin includes both open positions AND pending orders
    used_margin = positions_margin + pending_orders_margin

    # Calculate available margin consistently with order placement logic
    # Available Margin = Allotted Margin - Used Margin (positions + pending orders)
    available = margin_allotted - used_margin

    return {
        "data": {
            "available_margin": round(available, 2),
            "used_margin":      round(used_margin, 2),
            # total_balance now reflects actual allotted margin (for backward compatibility)
            "total_balance":    round(margin_allotted, 2),
            "allotted_margin":  round(margin_allotted, 2),
            "wallet_balance":   round(wallet_balance, 2),
        }
    }


# ── Admin / Debug endpoints ───────────────────────────────────────────────────

@router.get("/span-data")
async def get_span_data_endpoint(symbol: Optional[str] = Query(None)):
    """
    Return the cached SPAN® data for one or all symbols.
    Useful for debugging and verifying the daily download.

    GET /margin/span-data/?symbol=NIFTY   → single symbol
    GET /margin/span-data/                → all symbols (may be large)
    """
    store = get_store()
    if not store.ready:
        return {
            "ready": False,
            "message": "NSE margin data not yet loaded. "
                       "It loads at 08:45 IST and on startup.",
            "as_of": None,
        }

    if symbol:
        sym   = symbol.upper()
        entry = get_span_data(sym)
        if not entry:
            return {
                "ready":   True,
                "symbol":  sym,
                "found":   False,
                "as_of":   store.as_of.isoformat() if store.as_of else None,
            }
        return {
            "ready":      True,
            "symbol":     entry.symbol,
            "found":      True,
            "ref_price":  entry.ref_price,
            "price_scan": entry.price_scan,
            "cvf":        entry.cvf,
            "source":     entry.source,
            "as_of":      store.as_of.isoformat() if store.as_of else None,
        }

    # Return all (summary)
    all_entries = [
        {
            "symbol":     e.symbol,
            "ref_price":  e.ref_price,
            "price_scan": e.price_scan,
            "cvf":        e.cvf,
            "source":     e.source,
        }
        for e in store.span.values()
    ]
    return {
        "ready":        True,
        "count":        len(all_entries),
        "as_of":        store.as_of.isoformat() if store.as_of else None,
        "elm_oth_count": len(store.elm_oth),
        "elm_otm_count": len(store.elm_otm),
        "span":         all_entries,
    }


@router.post("/nse-refresh")
async def trigger_nse_refresh():
    """
    Manually trigger an immediate NSE margin data refresh.
    Normally this runs automatically at 08:45 IST.
    Requires admin privileges (enforced by the caller's auth middleware).
    """
    log.info("Manual NSE margin refresh triggered via API …")
    ok = await download_and_refresh()
    store = get_store()
    return {
        "success":       ok,
        "span_symbols":  len(store.span),
        "elm_oth_syms":  len(store.elm_oth),
        "elm_otm_syms":  len(store.elm_otm),
        "as_of":         store.as_of.isoformat() if store.as_of else None,
    }
