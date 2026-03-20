"""
app/routers/baskets.py
GET    /trading/basket-orders?user_id=X
POST   /trading/basket-orders              {user_id, name, legs:[]}
POST   /trading/basket-orders/execute      {basket_id, name, orders:[...]}
POST   /trading/basket-orders/{basket_id}/margin   Calculate required margin for basket
DELETE /trading/basket-orders/{id}
POST   /trading/basket-orders/{basket_id}/legs   {symbol, security_id, exchange, side, qty, productType, price}
"""
import logging
import uuid
from typing import List, Optional

from fastapi  import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel

from app.database import get_pool
from app.dependencies import CurrentUser, get_current_user
from app.margin.nse_margin_data import calculate_margin as _nse_calculate_margin
from app.market_hours import get_market_state, is_market_open

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/trading/basket-orders", tags=["Baskets"])


# Exchange freeze limits in number of lots.
_FREEZE_LOT_LIMITS = {
    "NIFTY": 24,
    "BANKNIFTY": 17,
    "FINNIFTY": 27,
    "MIDCPNIFTY": 20,
    "SENSEX": 50,
    "BANKEX": 30,
}


def _freeze_lot_limit_for_underlying(underlying: str) -> Optional[int]:
    if not underlying:
        return None
    key = str(underlying).upper().replace(" ", "")
    aliases = {
        "NIFTY50": "NIFTY",
        "MIDCPNIFTY": "MIDCPNIFTY",
        "FINNIFTY": "FINNIFTY",
    }
    key = aliases.get(key, key)
    return _FREEZE_LOT_LIMITS.get(key)


def _compute_slice_quantities(total_qty: int, max_qty_per_order: int) -> List[int]:
    if max_qty_per_order <= 0 or total_qty <= max_qty_per_order:
        return [total_qty]
    slices: List[int] = []
    remaining = int(total_qty)
    while remaining > max_qty_per_order:
        slices.append(max_qty_per_order)
        remaining -= max_qty_per_order
    if remaining > 0:
        slices.append(remaining)
    return slices


def _uid(request: Request, user_id_param, current_user: Optional[CurrentUser] = None) -> str:
    def _norm_uuid(raw: Optional[str]) -> Optional[str]:
        if raw is None:
            return None
        s = str(raw).strip()
        if not s:
            return None
        try:
            return str(uuid.UUID(s))
        except Exception:
            return None

    if current_user:
        caller_id = _norm_uuid(current_user.id)
    else:
        caller_id = None

    # Explicit query/body override: allow only valid UUID.
    if user_id_param is not None:
        uid_q = _norm_uuid(user_id_param)
        if not uid_q:
            raise HTTPException(status_code=422, detail="user_id must be a UUID")
        return uid_q

    # Legacy X-USER support (best effort): use only if valid UUID.
    hdr = request.headers.get("X-USER")
    uid_h = _norm_uuid(hdr)
    if uid_h:
        return uid_h

    if caller_id:
        return caller_id
    raise HTTPException(status_code=401, detail="Authentication required")


async def _resolve_security_id(pool, raw_security_id: Optional[str], symbol: Optional[str]) -> Optional[int]:
    sid = str(raw_security_id or "").strip()
    if sid.isdigit():
        return int(sid)

    sym = (symbol or "").strip()
    if not sym:
        return None

    try:
        row = await pool.fetchrow(
            "SELECT security_id FROM instrument_master WHERE symbol ILIKE $1 OR display_name ILIKE $1 LIMIT 1",
            sym,
        )
    except Exception:
        row = await pool.fetchrow(
            "SELECT instrument_token AS security_id FROM instrument_master WHERE symbol ILIKE $1 OR display_name ILIKE $1 LIMIT 1",
            sym,
        )
    if row and row.get("security_id"):
        return int(row["security_id"])

    import re
    m = re.match(r"(\w+)\s+(\d+)\s+(CE|PE)", sym, re.IGNORECASE)
    if not m:
        return None

    underlying_sym, strike, opt_type = m.groups()
    try:
        row = await pool.fetchrow(
            """
            SELECT security_id
            FROM instrument_master
            WHERE underlying = $1
              AND strike_price = $2
              AND option_type = $3
              AND expiry_date >= CURRENT_DATE
            ORDER BY expiry_date ASC
            LIMIT 1
            """,
            underlying_sym.upper(),
            float(strike),
            opt_type.upper(),
        )
    except Exception:
        row = await pool.fetchrow(
            """
            SELECT instrument_token AS security_id
            FROM instrument_master
            WHERE underlying = $1
              AND strike_price = $2
              AND option_type = $3
              AND expiry_date >= CURRENT_DATE
            ORDER BY expiry_date ASC
            LIMIT 1
            """,
            underlying_sym.upper(),
            float(strike),
            opt_type.upper(),
        )
    if row and row.get("security_id"):
        return int(row["security_id"])
    return None


async def _resolve_exchange(pool, raw_exchange: Optional[str], symbol: Optional[str]) -> str:
    ex = (raw_exchange or "").strip().upper()
    if ex:
        return ex

    sym = (symbol or "").strip()
    if not sym:
        return "NSE_FNO"

    row = await pool.fetchrow(
        "SELECT exchange_segment FROM instrument_master WHERE symbol ILIKE $1 OR display_name ILIKE $1 LIMIT 1",
        sym,
    )
    if row and row.get("exchange_segment"):
        return str(row["exchange_segment"]).strip().upper()

    return "NSE_FNO"


async def _resolve_instrument_row(pool, raw_security_id: Optional[str], symbol: Optional[str]):
    """Resolve instrument row safely without crashing when security_id column is unavailable."""
    sid = str(raw_security_id or "").strip()
    if sid.isdigit():
        sid_int = int(sid)
        try:
            row = await pool.fetchrow(
                "SELECT * FROM instrument_master WHERE security_id=$1 LIMIT 1",
                sid_int,
            )
            if row:
                return row
        except Exception:
            pass

        row = await pool.fetchrow(
            "SELECT * FROM instrument_master WHERE instrument_token=$1 LIMIT 1",
            sid_int,
        )
        if row:
            return row

    sym = (symbol or "").strip()
    if not sym:
        return None

    return await pool.fetchrow(
        "SELECT * FROM instrument_master WHERE symbol ILIKE $1 OR underlying ILIKE $1 LIMIT 1",
        sym,
    )


def _detect_instrument(symbol: str, exchange_segment: str) -> tuple[bool, bool, bool]:
    """Detect (is_option, is_futures, is_commodity) from symbol + segment."""
    sym = (symbol or "").upper()
    seg = (exchange_segment or "").upper()

    is_option  = (
        "OPT" in seg
        or sym.endswith("CE")
        or sym.endswith("PE")
        or "CE " in sym
        or "PE " in sym
    )
    is_futures = (
        not is_option
        and ("FUT" in seg or seg in ("NSE_FNO", "BSE_FNO", "MCX_FO", "NSE_COM"))
    )
    is_commodity = "MCX" in seg or "COM" in seg

    return is_option, is_futures, is_commodity


def _extract_underlying(symbol: str) -> str:
    """
    Extract underlying symbol from a full option/futures symbol.
    E.g.  "NIFTY24FEB25000CE" → "NIFTY"
          "BANKNIFTY"         → "BANKNIFTY"
          "RELIANCE"          → "RELIANCE"
    """
    import re
    sym = (symbol or "").upper().strip()
    # Strip trailing CE/PE
    for suffix in ("CE", "PE"):
        if sym.endswith(suffix):
            sym = sym[:-2]
            break
    # Try to match known index/stock prefixes
    m = re.match(r"^([A-Z&]+)", sym)
    if m:
        return m.group(1)
    return sym


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
        return {"total_margin": 0.0, "span_margin": 0.0, "exposure_margin": 0.0, "premium": 0.0, "elm_pct": 0.0}

    tx = (transaction_type or "BUY").upper()
    if is_option and tx == "BUY":
        premium = round(float(ltp or 0.0) * qty, 2)
        return {"total_margin": premium, "span_margin": 0.0, "exposure_margin": 0.0, "premium": premium, "elm_pct": 0.0}

    sym = (symbol or "").upper().strip()
    und = _extract_underlying(sym)
    row = await pool.fetchrow(
        """
        SELECT symbol, ref_price, price_scan, contract_value_factor, elm_pct
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
            "total_margin": None,
            "span_margin": None,
            "exposure_margin": None,
            "premium": None,
            "elm_pct": None,
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
        "total_margin": round(total_margin, 2),
        "span_margin": round(span_margin, 2),
        "exposure_margin": round(exposure_margin, 2),
        "premium": 0.0,
        "elm_pct": round(elm_pct, 4),
    }


# ── Pydantic models ────────────────────────────────────────────────────────

class LegModel(BaseModel):
    symbol:       Optional[str]   = None
    security_id:  Optional[str]   = None
    exchange:     Optional[str]   = "NSE_FNO"
    side:         Optional[str]   = "BUY"        # BUY / SELL
    qty:          Optional[int]   = 1
    productType:  Optional[str]   = "INTRADAY"
    price:        Optional[float] = 0.0
    order_type:   Optional[str]   = "MARKET"


class CreateBasketRequest(BaseModel):
    user_id: Optional[str]    = None
    name:    Optional[str]    = "Basket"
    legs:    Optional[List[LegModel]] = []


class ExecuteBasketRequest(BaseModel):
    basket_id: Optional[str]       = None
    name:      Optional[str]       = None
    orders:    Optional[List[dict]] = []


# ── Helpers ────────────────────────────────────────────────────────────────

async def _fetch_basket_with_legs(pool, basket_id: str) -> Optional[dict]:
    basket = await pool.fetchrow(
        "SELECT * FROM basket_orders WHERE id=$1::uuid", basket_id
    )
    if not basket:
        return None
    legs = await pool.fetch(
        "SELECT * FROM basket_order_legs WHERE basket_id=$1::uuid ORDER BY created_at",
        basket_id,
    )
    d = dict(basket)
    d["id"]   = str(d["id"])
    d["legs"] = [dict(l) for l in legs]
    for l in d["legs"]:
        l["id"]        = str(l.get("id", ""))
        l["basket_id"] = str(l.get("basket_id", ""))
    return d


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_baskets(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    user_id: Optional[str] = Query(None),
):
    uid  = _uid(request, user_id, current_user)
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM basket_orders WHERE user_id=$1 ORDER BY created_at DESC", uid
    )
    result = []
    for r in rows:
        b = await _fetch_basket_with_legs(pool, str(r["id"]))
        if b:
            result.append(b)
    return {"data": result}


@router.post("")
async def create_basket(
    body: CreateBasketRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    uid  = _uid(request, body.user_id, current_user)
    pool = get_pool()

    basket = await pool.fetchrow(
        "INSERT INTO basket_orders (user_id, name) VALUES ($1, $2) RETURNING *",
        uid, body.name or "Basket",
    )
    basket_id = str(basket["id"])

    for leg in (body.legs or []):
        resolved_sid = await _resolve_security_id(pool, leg.security_id, leg.symbol)
        resolved_ex = await _resolve_exchange(pool, leg.exchange, leg.symbol)
        await pool.execute(
            """
            INSERT INTO basket_order_legs
              (basket_id, symbol,   security_id, exchange,
               side,       qty,     product_type, price, order_type)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            basket_id,
            leg.symbol, resolved_sid, resolved_ex,
            leg.side,   leg.qty, leg.productType, leg.price, leg.order_type,
        )

    return {"success": True, "data": await _fetch_basket_with_legs(pool, basket_id)}


@router.post("/execute")
async def execute_basket(
    body: ExecuteBasketRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Place all legs of a basket as individual paper orders.
    Accepts either basket_id (db lookup) or inline orders list.
    Enforces SPAN margin before execution.
    """
    pool = get_pool()

    legs_to_execute: List[dict] = []

    if body.basket_id:
        basket = await pool.fetchrow(
            "SELECT * FROM basket_orders WHERE id=$1::uuid", body.basket_id
        )
        if not basket:
            raise HTTPException(status_code=404, detail="Basket not found")
        raw_legs = await pool.fetch(
            "SELECT * FROM basket_order_legs WHERE basket_id=$1::uuid", body.basket_id
        )
        legs_to_execute = [dict(l) for l in raw_legs]
    elif body.orders:
        legs_to_execute = body.orders
    else:
        raise HTTPException(status_code=400, detail="Provide basket_id or orders")

    uid = _uid(request, None, current_user)

    resolved_legs: List[dict] = []
    total_required_margin = 0.0

    for leg in legs_to_execute:
        security_id = leg.get("security_id") or leg.get("securityId") or ""
        symbol_raw = (leg.get("symbol") or "").strip()
        exchange = leg.get("exchange") or leg.get("exchange_segment") or leg.get("exchangeSegment") or ""
        side_u = (leg.get("side") or leg.get("transaction_type") or "BUY").upper()
        qty = int(leg.get("qty") or leg.get("quantity") or 1)
        product_u = str(leg.get("product_type") or leg.get("productType") or "MIS").upper()
        price = float(leg.get("price") or 0)
        order_type_u = str(leg.get("order_type") or "MARKET").upper()

        if qty <= 0:
            raise HTTPException(status_code=422, detail="Order quantity must be greater than 0")
        if side_u not in {"BUY", "SELL"}:
            raise HTTPException(status_code=422, detail=f"Invalid side: {side_u}")

        im_row = await _resolve_instrument_row(pool, security_id, symbol_raw)
        if not im_row:
            raise HTTPException(
                status_code=404,
                detail=f"Instrument not found for leg: {symbol_raw or security_id}",
            )

        token = int(im_row["instrument_token"])
        symbol = symbol_raw or str(im_row.get("symbol") or im_row.get("underlying") or token)
        lot_size = int(im_row.get("lot_size") or 1)
        underlying_for_freeze = str(im_row.get("underlying") or _extract_underlying(symbol) or "")

        if (not exchange) and im_row.get("exchange_segment"):
            exchange = str(im_row["exchange_segment"]).strip().upper()
        if not exchange:
            exchange = "NSE_FNO"

        if not is_market_open(exchange, symbol):
            market_state = get_market_state(exchange, symbol)
            raise HTTPException(
                status_code=403,
                detail=f"Market is {market_state.value}. Orders can only be placed during market hours.",
            )

        ltp_row = await pool.fetchrow(
            "SELECT ltp FROM market_data WHERE instrument_token=$1",
            token,
        )
        fill_price = float(ltp_row["ltp"]) if (ltp_row and ltp_row["ltp"]) else (price or 100.0)

        security_id_value = im_row.get("security_id")
        if security_id_value is None and str(security_id).isdigit():
            security_id_value = int(security_id)
        if security_id_value is None:
            security_id_value = token

        freeze_lots = _freeze_lot_limit_for_underlying(underlying_for_freeze)
        if freeze_lots:
            max_qty_per_order = int(freeze_lots) * max(1, lot_size)
            slice_quantities = _compute_slice_quantities(qty, max_qty_per_order)
        else:
            slice_quantities = [qty]

        for slice_qty in slice_quantities:
            if side_u == "BUY":
                is_option, is_futures, is_commodity = _detect_instrument(symbol, exchange)
                underlying = _extract_underlying(symbol)
                if "MCX" in (exchange or "").upper():
                    margin_breakdown = await _calculate_mcx_margin(
                        pool=pool,
                        symbol=underlying,
                        quantity=slice_qty,
                        ltp=fill_price,
                        transaction_type=side_u,
                        is_option=is_option,
                    )
                else:
                    margin_breakdown = _nse_calculate_margin(
                        symbol=underlying,
                        transaction_type=side_u,
                        quantity=slice_qty,
                        ltp=fill_price,
                        is_option=is_option,
                        is_futures=is_futures,
                        is_commodity=is_commodity,
                    )
                total_required_margin += float(margin_breakdown.get("total_margin") or 0.0)

            resolved_legs.append(
                {
                    "symbol": symbol,
                    "token": token,
                    "exchange": exchange,
                    "side": side_u,
                    "qty": int(slice_qty),
                    "product": product_u,
                    "order_type": order_type_u,
                    "fill_price": fill_price,
                    "limit_price": float(price) if order_type_u == "LIMIT" and float(price) > 0 else None,
                    "security_id": int(security_id_value),
                }
            )
    
    # ── Check available margin before execution ───────────────────────────────
    if total_required_margin > 0:
        margin_row = await pool.fetchrow(
            """
            SELECT
                COALESCE(pa.margin_allotted, 0) AS margin_allotted,
                COALESCE(SUM(
                    calculate_position_margin(
                        pp.instrument_token,
                        pp.symbol,
                        pp.exchange_segment,
                        pp.quantity,
                        pp.product_type
                    )
                ) FILTER (WHERE pp.status='OPEN' AND pp.quantity != 0), 0) AS used_margin
            FROM paper_accounts pa
            LEFT JOIN paper_positions pp ON pp.user_id = pa.user_id
            WHERE pa.user_id = $1::uuid
            GROUP BY pa.margin_allotted
            """,
            uid,
        )
        
        if not margin_row:
            raise HTTPException(status_code=403, detail="No margin account found for this user.")
        
        allotted = float(margin_row["margin_allotted"] or 0)
        used = float(margin_row["used_margin"] or 0)
        available = allotted - used
        
        if total_required_margin > available + 1e-6:
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient margin for basket execution. Required ₹{total_required_margin:.2f}, available ₹{available:.2f}."
            )

    results = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for leg in resolved_legs:
                symbol = leg["symbol"]
                token = leg["token"]
                exchange = leg["exchange"]
                side_u = leg["side"]
                qty = leg["qty"]
                product = leg["product"]
                order_type_u = leg["order_type"]
                fill_price = leg["fill_price"]
                limit_price = leg["limit_price"]
                security_id = leg["security_id"]

                order_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO paper_orders
                        (order_id, user_id, instrument_token, symbol, exchange_segment,
                         side, order_type, quantity, trigger_price, limit_price,
                         fill_price, filled_qty, status, product_type, security_id)
                    VALUES
                        ($1,$2,$3,$4,$5,$6,$7,$8,NULL,$9,$10,$11,'FILLED',$12,$13)
                    """,
                    order_id,
                    uid,
                    int(token),
                    symbol,
                    exchange,
                    side_u,
                    order_type_u,
                    int(qty),
                    limit_price,
                    float(fill_price),
                    int(qty),
                    product,
                    int(security_id),
                )

                if side_u == "BUY":
                    open_pos = await conn.fetchrow(
                        """
                        SELECT position_id, quantity, avg_price
                        FROM paper_positions
                        WHERE user_id = $1 AND instrument_token = $2 AND status = 'OPEN'
                        """,
                        uid,
                        token,
                    )
                    if open_pos:
                        existing_qty = int(open_pos["quantity"] or 0)
                        existing_avg = float(open_pos["avg_price"] or 0)
                        new_qty = existing_qty + int(qty)
                        new_avg = ((existing_avg * existing_qty) + (float(fill_price) * int(qty))) / new_qty
                        await conn.execute(
                            """
                            UPDATE paper_positions
                            SET quantity = $1, avg_price = $2
                            WHERE position_id = $3
                            """,
                            new_qty,
                            new_avg,
                            open_pos["position_id"],
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO paper_positions
                                (user_id, instrument_token, symbol, exchange_segment,
                                 quantity, avg_price, product_type, status)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,'OPEN')
                            """,
                            uid,
                            token,
                            symbol,
                            exchange,
                            int(qty),
                            float(fill_price),
                            product,
                        )
                else:
                    open_pos = await conn.fetchrow(
                        """
                        SELECT position_id, quantity, avg_price
                        FROM paper_positions
                        WHERE user_id = $1 AND instrument_token = $2 AND status = 'OPEN'
                        """,
                        uid,
                        token,
                    )
                    if open_pos:
                        existing_qty = int(open_pos["quantity"] or 0)
                        existing_avg = float(open_pos["avg_price"] or 0)
                        new_qty = max(0, existing_qty - int(qty))
                        if new_qty == 0:
                            realized_pnl = (float(fill_price) - existing_avg) * existing_qty
                            await conn.execute(
                                """
                                UPDATE paper_positions
                                SET quantity = 0, status = 'CLOSED',
                                    realized_pnl = $1, closed_at = NOW()
                                WHERE position_id = $2
                                """,
                                realized_pnl,
                                open_pos["position_id"],
                            )
                        else:
                            realized_pnl = (float(fill_price) - existing_avg) * int(qty)
                            await conn.execute(
                                """
                                UPDATE paper_positions
                                SET quantity = $1,
                                    realized_pnl = COALESCE(realized_pnl, 0) + $2
                                WHERE position_id = $3
                                """,
                                new_qty,
                                realized_pnl,
                                open_pos["position_id"],
                            )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO paper_positions
                                (user_id, instrument_token, symbol, exchange_segment,
                                 quantity, avg_price, product_type, status)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,'OPEN')
                            """,
                            uid,
                            token,
                            symbol,
                            exchange,
                            -int(qty),
                            float(fill_price),
                            product,
                        )

                results.append({"symbol": symbol, "status": "FILLED", "order_id": order_id})

    return {"success": True, "results": results}


@router.delete("/{basket_id}")
async def delete_basket(basket_id: str = Path(...)):
    pool = get_pool()
    await pool.execute(
        "DELETE FROM basket_order_legs WHERE basket_id=$1::uuid", basket_id
    )
    result = await pool.execute(
        "DELETE FROM basket_orders WHERE id=$1::uuid", basket_id
    )
    if "DELETE 0" in str(result):
        raise HTTPException(status_code=404, detail="Basket not found")
    return {"success": True}


@router.post("/{basket_id}/legs")
async def add_leg(body: LegModel, basket_id: str = Path(...)):
    pool = get_pool()

    basket = await pool.fetchrow(
        "SELECT id FROM basket_orders WHERE id=$1::uuid", basket_id
    )
    if not basket:
        raise HTTPException(status_code=404, detail="Basket not found")

    resolved_sid = await _resolve_security_id(pool, body.security_id, body.symbol)
    resolved_ex = await _resolve_exchange(pool, body.exchange, body.symbol)

    leg = await pool.fetchrow(
        """
        INSERT INTO basket_order_legs
          (basket_id, symbol, security_id, exchange,
           side, qty, product_type, price, order_type)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        RETURNING *
        """,
        basket_id,
        body.symbol, resolved_sid, resolved_ex,
        body.side, body.qty, body.productType, body.price, body.order_type,
    )
    d = dict(leg)
    d["id"]        = str(d["id"])
    d["basket_id"] = str(d["basket_id"])
    return {"success": True, "data": d}


@router.post("/{basket_id}/margin")
async def calculate_basket_margin(basket_id: str = Path(...)):
    """
    Calculate the total required margin for a basket.
    Returns breakdown of SPAN + exposure for all BUY legs.
    """
    pool = get_pool()
    
    basket = await pool.fetchrow(
        "SELECT * FROM basket_orders WHERE id=$1::uuid", basket_id
    )
    if not basket:
        raise HTTPException(status_code=404, detail="Basket not found")
    
    legs = await pool.fetch(
        "SELECT * FROM basket_order_legs WHERE basket_id=$1::uuid", basket_id
    )
    
    total_margin = 0.0
    total_span = 0.0
    total_exposure = 0.0
    leg_breakdowns = []
    
    for leg in legs:
        symbol = leg["symbol"] or ""
        exchange = leg["exchange"] or "NSE_FNO"
        side = (leg["side"] or "BUY").upper()
        qty = int(leg["qty"] or 0)
        price = float(leg["price"] or 0)
        
        # Get LTP if price not available
        if price == 0:
            security_id = leg.get("security_id") or ""
            im_row = None
            if security_id and str(security_id).isdigit():
                im_row = await pool.fetchrow(
                    "SELECT instrument_token FROM instrument_master WHERE security_id=$1 LIMIT 1",
                    str(security_id),
                )
            if not im_row and symbol:
                im_row = await pool.fetchrow(
                    "SELECT instrument_token FROM instrument_master WHERE symbol ILIKE $1 OR underlying ILIKE $1 LIMIT 1", 
                    symbol
                )
            if im_row:
                ltp_row = await pool.fetchrow(
                    "SELECT ltp FROM market_data WHERE instrument_token=$1",
                    im_row["instrument_token"],
                )
                if ltp_row and ltp_row["ltp"]:
                    price = float(ltp_row["ltp"])
        
        # Calculate margin using SPAN (only for BUY orders)
        leg_margin = 0.0
        if side == "BUY" and price > 0 and qty > 0:
            is_option, is_futures, is_commodity = _detect_instrument(symbol, exchange)
            underlying = _extract_underlying(symbol)
            
            if "MCX" in (exchange or "").upper():
                breakdown = await _calculate_mcx_margin(
                    pool=pool,
                    symbol=underlying,
                    quantity=qty,
                    ltp=price,
                    transaction_type=side,
                    is_option=is_option,
                )
            else:
                breakdown = _nse_calculate_margin(
                    symbol=underlying,
                    transaction_type=side,
                    quantity=qty,
                    ltp=price,
                    is_option=is_option,
                    is_futures=is_futures,
                    is_commodity=is_commodity,
                )
            
            leg_margin = breakdown["total_margin"]
            total_margin += leg_margin
            total_span += breakdown["span_margin"]
            total_exposure += breakdown["exposure_margin"]
            
            leg_breakdowns.append({
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "margin": leg_margin,
                "span": breakdown["span_margin"],
                "exposure": breakdown["exposure_margin"]
            })
    
    return {
        "success": True,
        "data": {
            "basket_id": basket_id,
            "basket_name": basket["name"],
            "total_required_margin": round(total_margin, 2),
            "total_span_margin": round(total_span, 2),
            "total_exposure_margin": round(total_exposure, 2),
            "legs": leg_breakdowns
        }
    }

