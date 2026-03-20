"""
app/routers/orders.py  (v2 — frontend-compatible prefix + API shape)
"""
import logging
import uuid
from typing import List, Optional
import json

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, validator, Field

from app.database                              import get_pool
from app.dependencies                          import CurrentUser, get_current_user
from app.execution_simulator.execution_engine import is_mock_mode
from app.execution_simulator.fill_engine import execute_market_fill
from app.execution_simulator.execution_config import get_tick_size
from app.margin.nse_margin_data               import calculate_margin as _nse_calculate_margin
from app.market_hours                          import is_market_open, get_market_state
from decimal                                   import Decimal
from app.execution_simulator.order_queue_manager import QueuedOrder, enqueue as _queue_enqueue

log = logging.getLogger(__name__)

router = APIRouter(prefix="/trading/orders", tags=["Orders"])


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


def _is_commodity_segment(exchange_segment: str) -> bool:
    seg = (exchange_segment or "").upper()
    return (
        "MCX" in seg
        or "NCDEX" in seg
        or "COMM" in seg
    )


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


async def _calculate_required_margin(
    pool,
    price: float, 
    qty: int, 
    exchange_segment: str, 
    product_type: str, 
    symbol: str,
    transaction_type: str = "BUY",
    instrument_type: Optional[str] = None,
    option_type: Optional[str] = None,
) -> dict:
    """
    Calculate required margin using NSE SPAN + ELM data.
    Returns dict with total_margin and breakdown.
    """
    if qty <= 0:
        return {"total_margin": 0.0, "span_margin": 0.0, "exposure_margin": 0.0, "premium": 0.0}
    
    is_option, is_futures, is_commodity = _detect_instrument(
        symbol,
        exchange_segment,
        instrument_type=instrument_type,
        option_type=option_type,
    )
    is_equity = not is_option and not is_futures and not is_commodity
    underlying = _extract_underlying(symbol)

    # Cash equity margin: qty × price
    if is_equity:
        cash = round(float(price or 0) * int(qty), 2)
        return {
            "total_margin": cash,
            "span_margin": 0.0,
            "exposure_margin": 0.0,
            "premium": cash,
        }

    # Option BUY margin: premium only
    if is_option and transaction_type.upper() == "BUY":
        premium = round(float(price or 0) * int(qty), 2)
        return {
            "total_margin": premium,
            "span_margin": 0.0,
            "exposure_margin": 0.0,
            "premium": premium,
        }

    if "MCX" in (exchange_segment or "").upper():
        breakdown = await _calculate_mcx_margin(
            pool=pool,
            symbol=underlying,
            quantity=int(qty),
            ltp=float(price or 0),
            transaction_type=transaction_type,
            is_option=is_option,
        )
    else:
        breakdown = _nse_calculate_margin(
            symbol=underlying,
            transaction_type=transaction_type,
            quantity=int(qty),
            ltp=float(price or 0),
            is_option=is_option,
            is_futures=is_futures,
            is_commodity=is_commodity,
        )

    return breakdown


def _uid(request: Request, user_id_param) -> str:
    if user_id_param:
        return str(user_id_param)
    hdr = request.headers.get("X-USER")
    return hdr if hdr else "default"


class PlaceOrderRequest(BaseModel):
    user_id:          Optional[str]   = None
    symbol:           Optional[str]   = None
    security_id:      Optional[int]   = None
    instrument_token: Optional[int]   = None
    exchange_segment: Optional[str]   = Field(None, pattern="^(NSE_EQ|NSE_FNO|BSE_EQ|BSE_FNO|MCX_FO|NSE_COM)$")
    transaction_type: Optional[str]   = None
    side:             Optional[str]   = Field(None, pattern="^(BUY|SELL)$")
    quantity:         int             = Field(default=1, gt=0)
    order_type:       str             = "MARKET"
    product_type:     str             = "NORMAL"
    price:            Optional[float] = None
    limit_price:      Optional[float] = None
    trigger_price:    Optional[float] = None
    is_super:         bool            = False
    target_price:     Optional[float] = None
    stop_loss_price:  Optional[float] = None
    trailing_jump:    Optional[float] = None
    skip_slicing:     bool            = False

    @validator('symbol', 'exchange_segment', pre=True)
    def empty_str_to_none(cls, v):
        if v == "" or v is None:
            return ""
        return str(v).strip()

    @validator('security_id', 'instrument_token', pre=True)
    def empty_int_to_none(cls, v):
        if v == "" or v is None:
            return None
        return int(v)

    @validator('quantity', pre=True)
    def coerce_quantity(cls, v):
        if v is None or v == "":
            return 1
        return int(v)

    @validator('is_super', pre=True)
    def coerce_bool(cls, v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.lower() in ('true', '1', 'yes')
        return bool(v)


@router.post("", status_code=status.HTTP_201_CREATED)
async def place_paper_order(
    body: PlaceOrderRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        # Authorization: Only ADMIN/SUPER_ADMIN can place orders for other users
        if body.user_id and str(body.user_id) != str(current_user.id):
            if current_user.role not in ("ADMIN", "SUPER_ADMIN"):
                raise HTTPException(
                    status_code=403,
                    detail="You can only place orders for yourself. Admin access required to place orders for other users."
                )
        
        user_id   = body.user_id or current_user.id
        pool      = get_pool()
        
        log.info(f"Order placement attempt - User: {current_user.id} (role: {current_user.role}), Target: {user_id}, Symbol: {body.symbol}, Side: {body.side or body.transaction_type}")

        # ── User status enforcement ────────────────────────────────────────────
        try:
            uuid.UUID(str(user_id))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid user_id")

        try:
            _status_row = await pool.fetchrow(
                "SELECT status FROM users WHERE id=$1::uuid", user_id
            )
        except Exception as exc:
            log.warning("User status check skipped: %s", exc)
            _status_row = None

        if _status_row:
            _us = _status_row["status"]
            if _us == "BLOCKED":
                raise HTTPException(
                    status_code=403,
                    detail="Your account is blocked. You can only submit payout requests.",
                )
            if _us == "SUSPENDED":
                raise HTTPException(
                    status_code=403,
                    detail="Your account is suspended. You can only exit existing positions.",
                )
            if _us == "PENDING":
                raise HTTPException(
                    status_code=403,
                    detail="Your account is pending activation. Please contact your admin.",
                )
        token     = body.security_id or body.instrument_token

        if not token:
            if body.symbol and body.symbol.strip():
                symbol_clean = body.symbol.strip()
                
                # Try exact match first
                row = await pool.fetchrow(
                    "SELECT instrument_token FROM instrument_master WHERE symbol ILIKE $1 OR display_name ILIKE $1 LIMIT 1",
                    symbol_clean,
                )
                
                # If no match, try parsing option format "NIFTY 25500 CE" and construct display_name pattern
                if not row and (" CE" in symbol_clean.upper() or " PE" in symbol_clean.upper()):
                    import re
                    # Parse "NIFTY 25500 CE" format
                    match = re.match(r"(\w+)\s+(\d+)\s+(CE|PE)", symbol_clean, re.IGNORECASE)
                    if match:
                        underlying_sym, strike, opt_type = match.groups()
                        # Try display_name pattern like "NIFTY-Mar2026-25500-CE"
                        row = await pool.fetchrow(
                            """
                            SELECT instrument_token FROM instrument_master 
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
                
                if row:
                    token = row["instrument_token"]
                else:
                    log.error(f"Order placement failed: Symbol '{body.symbol}' not found in instrument_master")
                    raise HTTPException(
                        status_code=404,
                        detail=f"Instrument not found: {body.symbol}"
                    )
            else:
                log.error(f"Order placement failed: No instrument identifier provided (no token and no symbol)")
                raise HTTPException(
                    status_code=400,
                    detail="Either security_id/instrument_token or symbol must be provided"
                )

        # ── Normalize/repair exchange_segment from instrument_master (robustness) ──
        # Look up exchange_segment from instrument_master if not provided
        seg_in = (body.exchange_segment or "").strip().upper()
        inst_type = None
        opt_type = None
        if token and token != 0:
            im_seg_row = await pool.fetchrow(
                """
                SELECT exchange_segment, instrument_type, option_type, lot_size, underlying, symbol
                FROM instrument_master
                WHERE instrument_token=$1
                """,
                int(token),
            )
            im_seg = (im_seg_row["exchange_segment"] if im_seg_row else None) or None
            im_seg_u = str(im_seg).strip().upper() if im_seg else ""
            if im_seg_row:
                inst_type = im_seg_row["instrument_type"]
                opt_type = im_seg_row["option_type"]

            # Use database value if not provided or generic exchange name given
            if (not seg_in) or seg_in in {"NSE", "BSE", "MCX"}:
                if im_seg_u:
                    body.exchange_segment = im_seg_u

        side     = (body.transaction_type or body.side or "BUY").upper()
        qty      = body.quantity
        ord_type = body.order_type.upper()
        prod     = body.product_type.upper()
        lp       = body.limit_price or body.price

        # ── Auto-slice oversized orders based on exchange freeze limits (lots) ──
        if not body.skip_slicing and qty > 0:
            im_lot_size = int((im_seg_row["lot_size"] if im_seg_row else 0) or 1)
            inferred_symbol = body.symbol or (im_seg_row["symbol"] if im_seg_row else "") or ""
            inferred_underlying = (
                (im_seg_row["underlying"] if im_seg_row else None)
                or _extract_underlying(inferred_symbol)
            )
            freeze_lots = _freeze_lot_limit_for_underlying(str(inferred_underlying or ""))
            if freeze_lots:
                max_qty_per_order = int(freeze_lots) * max(1, im_lot_size)
                if qty > max_qty_per_order:
                    slice_quantities = _compute_slice_quantities(qty, max_qty_per_order)
                    child_results = []
                    for slice_qty in slice_quantities:
                        child_payload = body.dict()
                        child_payload["quantity"] = int(slice_qty)
                        child_payload["skip_slicing"] = True
                        child_body = PlaceOrderRequest(**child_payload)
                        child_result = await place_paper_order(child_body, request, current_user)
                        child_results.append(child_result)

                    child_order_ids = [
                        str(r.get("order_id"))
                        for r in child_results
                        if isinstance(r, dict) and r.get("order_id")
                    ]
                    first_order_id = child_order_ids[0] if child_order_ids else None
                    return {
                        "order_id": first_order_id,
                        "status": "SLICED",
                        "requested_qty": int(qty),
                        "slice_count": len(slice_quantities),
                        "max_qty_per_slice": int(max_qty_per_order),
                        "underlying": str(inferred_underlying or ""),
                        "lot_size": int(im_lot_size),
                        "freeze_lots": int(freeze_lots),
                        "child_order_ids": child_order_ids,
                        "children": child_results,
                    }

        # Commodity does not support MIS in this system.
        if _is_commodity_segment(body.exchange_segment or "") and prod == "MIS":
            raise HTTPException(
                status_code=400,
                detail="MIS is not allowed for commodity instruments. Use NORMAL product type."
            )

        # Get market snapshot for fill simulation
        md_row = await pool.fetchrow(
            "SELECT ltp, bid_depth, ask_depth, ltt FROM market_data WHERE instrument_token=$1", token
        ) if token else None
        ltp_price = float(md_row["ltp"]) if (md_row and md_row["ltp"] is not None) else None

        def _parse_depth(raw) -> list[dict]:
            if raw is None:
                return []
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    return []
            if not isinstance(raw, list):
                return []
            out: list[dict] = []
            for lvl in raw:
                if not isinstance(lvl, dict):
                    continue
                try:
                    px = float(lvl.get("price")) if lvl.get("price") is not None else None
                except (TypeError, ValueError):
                    px = None
                if px is None:
                    continue
                try:
                    q = int(lvl.get("qty")) if lvl.get("qty") is not None else 0
                except (TypeError, ValueError):
                    q = 0
                out.append({"price": px, "qty": max(0, q)})
            return out[:5]

        market_bid_depth = _parse_depth(md_row["bid_depth"]) if md_row else []
        market_ask_depth = _parse_depth(md_row["ask_depth"]) if md_row else []

        market_filled_qty = qty
        market_status = "FILLED"

        if ord_type == "MARKET":
            tick_size = get_tick_size(body.exchange_segment or "NSE_FNO")
            lot_size = int((im_seg_row["lot_size"] if im_seg_row else 0) or 1)

            class _MarketExecOrder:
                pass

            _m = _MarketExecOrder()
            _m.side = side
            _m.quantity = qty
            _m.exchange_segment = body.exchange_segment or "NSE_FNO"
            _m.limit_price = None

            fills = execute_market_fill(
                _m,
                {
                    "ltp": ltp_price,
                    "bid_depth": market_bid_depth,
                    "ask_depth": market_ask_depth,
                    "ltt": md_row["ltt"] if md_row else None,
                    "tick_size": float(tick_size),
                },
                tick_size,
                lot_size,
            )

            valid_fills = [f for f in fills if getattr(f, "fill_qty", 0) > 0]
            market_filled_qty = int(sum(f.fill_qty for f in valid_fills))

            if market_filled_qty > 0:
                weighted = sum(Decimal(str(f.fill_price)) * Decimal(f.fill_qty) for f in valid_fills)
                avg_px = weighted / Decimal(market_filled_qty)
                fill_price = float(avg_px)
                if market_filled_qty < qty:
                    market_status = "PARTIAL"
            else:
                # No executable liquidity across the ladder.
                fill_price = float(ltp_price or lp or 0.0)
                market_status = "REJECTED"

        elif ord_type in {"LIMIT", "SLL"}:
            if lp is None or float(lp) <= 0:
                raise HTTPException(status_code=400, detail="Valid limit_price is required for LIMIT/SLL orders")
            fill_price = float(lp)
        else:
            fill_price = ltp_price if ltp_price is not None else float(lp or 100.0)

        # ── Determine order type for margin calculation ──
        # MARKET orders: use fill_price (LTP). LIMIT/SL/SLL orders: use limit_price for margin calc
        margin_calc_price = fill_price  # MARKET and LIMIT use their respective prices

        if ord_type in {"SLM", "SLL"}:
            if not body.trigger_price or float(body.trigger_price) <= 0:
                raise HTTPException(status_code=400, detail="Valid trigger_price is required for SLM/SLL orders")

        order_id = str(uuid.uuid4())

        # ── Market hours validation ────────────────────────────────────────────
        if not is_market_open(body.exchange_segment, body.symbol):
            market_state = get_market_state(body.exchange_segment, body.symbol)
            
            # Log REJECTED order for audit trail
            await pool.execute(
                """
                INSERT INTO paper_orders
                    (order_id, user_id, instrument_token, symbol, exchange_segment,
                     side, order_type, quantity, limit_price, trigger_price, fill_price, filled_qty,
                     status, product_type, security_id, placed_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 0, 'REJECTED', $12, $13, NOW())
                """,
                order_id, user_id, token or 0, body.symbol, body.exchange_segment,
                side, ord_type, qty, lp, body.trigger_price, fill_price, prod, token or 0
            )
            
            raise HTTPException(
                status_code=403,
                detail=f"Market is {market_state.value}. Orders can only be placed during market hours."
            )

        # Track any PENDING SL orders that need to be removed from the in-memory queue
        # after the position is fully closed in the transaction below.
        _sl_to_cancel: list[dict] = []

        # ── Margin enforcement & Order placement in transaction (prevents race condition) ──
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Determine whether this order increases risk exposure (open/add/flip remainder).
                open_pos_for_margin = await conn.fetchrow(
                    """
                    SELECT quantity
                    FROM paper_positions
                    WHERE user_id = $1 AND instrument_token = $2 AND status = 'OPEN'
                    """,
                    user_id, token or 0
                )
                existing_qty_for_margin = int(open_pos_for_margin["quantity"] or 0) if open_pos_for_margin else 0
                signed_delta_for_margin = qty if side == "BUY" else -qty
                pending_same_side_qty = int(
                    await conn.fetchval(
                        """
                        SELECT COALESCE(SUM(GREATEST(COALESCE(quantity, 0) - COALESCE(filled_qty, 0), 0)), 0)
                        FROM paper_orders
                        WHERE user_id = $1
                          AND instrument_token = $2
                          AND side = $3
                          AND status IN ('PENDING', 'PARTIAL', 'PARTIAL_FILL', 'PARTIALLY_FILLED', 'OPEN')
                        """,
                        user_id,
                        token or 0,
                        side,
                    )
                    or 0
                )

                exposure_increase_qty = 0
                if existing_qty_for_margin == 0:
                    exposure_increase_qty = abs(signed_delta_for_margin)
                else:
                    same_direction = (
                        (existing_qty_for_margin > 0 and signed_delta_for_margin > 0)
                        or (existing_qty_for_margin < 0 and signed_delta_for_margin < 0)
                    )
                    if same_direction:
                        exposure_increase_qty = abs(signed_delta_for_margin)
                    else:
                        # Treat duplicate exits as fresh risk once pending close qty already
                        # consumes the currently open position.
                        close_capacity = max(
                            0,
                            abs(existing_qty_for_margin) - pending_same_side_qty,
                        )
                        close_part = min(abs(signed_delta_for_margin), close_capacity)
                        exposure_increase_qty = abs(signed_delta_for_margin) - close_part

                # Lock paper_accounts row to prevent concurrent order race condition
                if exposure_increase_qty > 0:
                    margin_breakdown = await _calculate_required_margin(
                        conn,
                        fill_price,
                        exposure_increase_qty,
                        body.exchange_segment,
                        prod,
                        body.symbol,
                        side,
                        instrument_type=inst_type,
                        option_type=opt_type,
                    )
                    required = margin_breakdown.get("total_margin")
                    if required is None:
                        raise HTTPException(
                            status_code=503,
                            detail=margin_breakdown.get("error")
                            or "Margin data not available for this symbol right now.",
                        )

                    # Lock account row first (race-safe and PostgreSQL-compatible)
                    account_row = await conn.fetchrow(
                        """
                        SELECT COALESCE(margin_allotted, 0) AS margin_allotted
                        FROM paper_accounts
                        WHERE user_id = $1::uuid
                        FOR UPDATE
                        """,
                        user_id,
                    )
                    if not account_row:
                        raise HTTPException(status_code=403, detail="No margin account found for this user.")

                    try:
                        # Calculate used margin from positions + pending orders
                        positions_margin = await conn.fetchval(
                            """
                            SELECT COALESCE(SUM(
                                calculate_position_margin(
                                    pp.instrument_token,
                                    pp.symbol,
                                    pp.exchange_segment,
                                    pp.quantity,
                                    pp.product_type
                                )
                            ), 0)
                            FROM paper_positions pp
                            WHERE pp.user_id = $1::uuid
                              AND pp.status = 'OPEN'
                              AND pp.quantity != 0
                            """,
                            user_id,
                        )
                        
                        # Add margin reserved by pending orders
                        pending_orders_margin = await conn.fetchval(
                            """
                            SELECT COALESCE(calculate_pending_orders_margin($1::uuid), 0)
                            """,
                            user_id,
                        )
                        
                        used_margin = float(positions_margin or 0) + float(pending_orders_margin or 0)
                    except Exception as margin_exc:
                        # Fallback for environments where DB margin function is missing/outdated.
                        log.warning("Used-margin function unavailable; falling back to notional calc: %s", margin_exc)
                        used_margin = await conn.fetchval(
                            """
                            SELECT COALESCE(SUM(ABS(pp.quantity * pp.avg_price)), 0)
                            FROM paper_positions pp
                            WHERE pp.user_id = $1::uuid
                              AND pp.status = 'OPEN'
                              AND pp.quantity != 0
                            """,
                            user_id,
                        )

                    allotted = float(account_row["margin_allotted"] or 0)
                    used = float(used_margin or 0)
                    available = allotted - used

                    if allotted <= 0:
                        raise HTTPException(status_code=403, detail="No margin allotted. Please contact your admin.")
                    if required > available + 1e-6:
                        raise HTTPException(
                            status_code=403,
                            detail=(
                                f"Insufficient margin. Required {required:.2f}, available {available:.2f}."
                            ),
                        )

                # ── SL orders: record as PENDING — position updated when trigger fires ──
                if ord_type in {"SLM", "SLL"}:
                    await conn.execute(
                        """
                        INSERT INTO paper_orders
                            (order_id, user_id, instrument_token, symbol, exchange_segment,
                             side, order_type, quantity, limit_price, trigger_price, fill_price, filled_qty,
                             status, product_type)
                        VALUES
                            ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,NULL,0,'PENDING',$11)
                        """,
                        order_id, user_id, token or 0, body.symbol, body.exchange_segment,
                        side, ord_type, qty, lp, body.trigger_price, prod,
                    )
                    _is_sl = True
                elif ord_type == "LIMIT":
                    # ── LIMIT: Immediate partial fill attempt, then queue remaining ──
                    tick_size = get_tick_size(body.exchange_segment or "NSE_FNO")
                    lot_size = int((im_seg_row["lot_size"] if im_seg_row else 0) or 1)

                    class _LimitExecOrder:
                        pass

                    _l = _LimitExecOrder()
                    _l.side = side
                    _l.quantity = qty
                    _l.exchange_segment = body.exchange_segment or "NSE_FNO"
                    _l.limit_price = lp

                    fills = execute_market_fill(
                        _l,
                        {
                            "ltp": ltp_price,
                            "bid_depth": market_bid_depth,
                            "ask_depth": market_ask_depth,
                            "ltt": md_row["ltt"] if md_row else None,
                            "tick_size": float(tick_size),
                        },
                        tick_size,
                        lot_size,
                    )

                    valid_fills = [f for f in fills if getattr(f, "fill_qty", 0) > 0]
                    limit_filled_qty = int(sum(f.fill_qty for f in valid_fills))
                    limit_remaining_qty = qty - limit_filled_qty

                    if limit_filled_qty > 0:
                        weighted = sum(Decimal(str(f.fill_price)) * Decimal(f.fill_qty) for f in valid_fills)
                        avg_px = weighted / Decimal(limit_filled_qty)
                        fill_price = float(avg_px)
                        limit_status = "PARTIAL" if limit_remaining_qty > 0 else "FILLED"
                    else:
                        fill_price = float(lp)
                        limit_status = "PENDING"

                    await conn.execute(
                        """
                        INSERT INTO paper_orders
                            (order_id, user_id, instrument_token, symbol, exchange_segment,
                             side, order_type, quantity, limit_price, trigger_price, fill_price, filled_qty,
                             remaining_qty, status, product_type)
                        VALUES
                            ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
                        """,
                        order_id, user_id, token or 0, body.symbol, body.exchange_segment,
                        side, ord_type, qty, lp, body.trigger_price, fill_price, limit_filled_qty,
                        limit_remaining_qty, limit_status, prod,
                    )
                    
                    # Record fills in paper_trades
                    for fill in valid_fills:
                        await conn.execute(
                            """
                            INSERT INTO paper_trades
                                (order_id, user_id, instrument_token, exchange_segment, symbol,
                                 side, fill_qty, fill_price, slippage)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                            """,
                            order_id, user_id, token or 0, body.exchange_segment, body.symbol,
                            side, fill.fill_qty, fill.fill_price, fill.slippage,
                        )

                    _is_sl = (limit_status in {"PENDING", "PARTIAL"})  # Queue only if not fully filled
                else:
                    # Insert order record (MARKET only — fill immediately at LTP)
                    await conn.execute(
                        """
                        INSERT INTO paper_orders
                            (order_id, user_id, instrument_token, symbol, exchange_segment,
                             side, order_type, quantity, limit_price, trigger_price, fill_price, filled_qty,
                             status, product_type)
                        VALUES
                            ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                        """,
                        order_id, user_id, token or 0, body.symbol, body.exchange_segment,
                        side, ord_type, qty, lp, body.trigger_price, fill_price, market_filled_qty,
                        market_status, prod,
                    )
                    _is_sl = False

                # Update or create signed position (handles long/short open, partial close, full close, flip)
                # SL orders stay PENDING; position update deferred to trigger fill
                if not _is_sl:
                    exec_qty = market_filled_qty if ord_type == "MARKET" else qty
                    if exec_qty <= 0:
                        signed_delta = 0
                    else:
                        signed_delta = exec_qty if side == "BUY" else -exec_qty

                    if signed_delta == 0:
                        open_pos = None
                    else:
                        open_pos = await conn.fetchrow(
                            """
                            SELECT position_id, quantity, avg_price, realized_pnl
                            FROM paper_positions
                            WHERE user_id = $1 AND instrument_token = $2 AND status = 'OPEN'
                            """,
                            user_id, token or 0
                        )

                    if signed_delta != 0 and not open_pos:
                        await conn.execute(
                            """
                            INSERT INTO paper_positions
                                (user_id, instrument_token, symbol, exchange_segment,
                                 quantity, avg_price, product_type, status)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,'OPEN')
                            """,
                            user_id, token or 0, body.symbol, body.exchange_segment,
                            signed_delta, fill_price, prod,
                        )
                    elif signed_delta != 0 and open_pos:
                        existing_qty = int(open_pos["quantity"] or 0)
                        existing_avg = float(open_pos["avg_price"] or 0)
                        existing_realized = float(open_pos["realized_pnl"] or 0)

                        if existing_qty == 0:
                            await conn.execute(
                                """
                                UPDATE paper_positions
                                SET quantity = $1,
                                    avg_price = $2,
                                    status = 'OPEN',
                                    closed_at = NULL
                                WHERE position_id = $3
                                """,
                                signed_delta,
                                fill_price,
                                open_pos["position_id"],
                            )
                        else:
                            existing_sign = 1 if existing_qty > 0 else -1
                            delta_sign = 1 if signed_delta > 0 else -1

                            if existing_sign == delta_sign:
                                # Increase same-direction position (average-in)
                                new_qty = existing_qty + signed_delta
                                new_avg = (
                                    (existing_avg * abs(existing_qty) + fill_price * abs(signed_delta))
                                    / abs(new_qty)
                                )
                                await conn.execute(
                                    """
                                    UPDATE paper_positions
                                    SET quantity = $1,
                                        avg_price = $2,
                                        status = 'OPEN',
                                        closed_at = NULL
                                    WHERE position_id = $3
                                    """,
                                    new_qty,
                                    new_avg,
                                    open_pos["position_id"],
                                )
                            else:
                                # Opposite-side order: reduce/close/flip existing position
                                close_qty = min(abs(existing_qty), abs(signed_delta))
                                realized_delta = (fill_price - existing_avg) * close_qty * existing_sign
                                new_qty = existing_qty + signed_delta

                                if new_qty == 0:
                                    # Fully closed
                                    await conn.execute(
                                        """
                                        UPDATE paper_positions
                                        SET quantity = 0,
                                            status = 'CLOSED',
                                            realized_pnl = $1,
                                            closed_at = NOW()
                                        WHERE position_id = $2
                                        """,
                                        existing_realized + realized_delta,
                                        open_pos["position_id"],
                                    )
                                    # Cancel any remaining PENDING orders for this instrument/user
                                    pending_sl_rows = await conn.fetch(
                                        """
                                        UPDATE paper_orders
                                        SET status = 'CANCELLED', updated_at = NOW()
                                        WHERE user_id = $1 AND instrument_token = $2
                                          AND status = 'PENDING'
                                        RETURNING order_id, side, limit_price
                                        """,
                                        user_id, int(token or 0),
                                    )
                                    for prow in pending_sl_rows:
                                        _sl_to_cancel.append({
                                            "order_id":         str(prow["order_id"]),
                                            "instrument_token": int(token or 0),
                                            "side":             prow["side"],
                                            "limit_price":      Decimal(str(prow["limit_price"] or 0)),
                                        })
                                elif (new_qty > 0 and existing_qty > 0) or (new_qty < 0 and existing_qty < 0):
                                    # Partial close, direction unchanged
                                    await conn.execute(
                                        """
                                        UPDATE paper_positions
                                        SET quantity = $1,
                                            realized_pnl = $2
                                        WHERE position_id = $3
                                        """,
                                        new_qty,
                                        existing_realized + realized_delta,
                                        open_pos["position_id"],
                                    )
                                else:
                                    # Flipped position: reopen remainder at fill price
                                    await conn.execute(
                                        """
                                        UPDATE paper_positions
                                        SET quantity = $1,
                                            avg_price = $2,
                                            status = 'OPEN',
                                            realized_pnl = $3,
                                            closed_at = NULL
                                        WHERE position_id = $4
                                        """,
                                        new_qty,
                                        fill_price,
                                        existing_realized + realized_delta,
                                        open_pos["position_id"],
                                    )

        # After transaction: flush auto-cancelled SL orders from in-memory queue
        if _sl_to_cancel:
            from app.execution_simulator.order_queue_manager import cancel_by_id as _queue_cancel_by_id
            for _item in _sl_to_cancel:
                await _queue_cancel_by_id(_item["order_id"])
                log.info("Auto-cancelled pending SL order %s (position fully closed)", _item["order_id"])

        # ── SL & LIMIT order: enqueue for trigger-based/market-based fill, return PENDING ──
        if ord_type in {"SLM", "SLL", "LIMIT"}:
            from app.execution_simulator.execution_config import get_tick_size as _get_tick_size
            tick_size = _get_tick_size(body.exchange_segment or "NSE_FNO")
            im_lot_row = await pool.fetchrow(
                "SELECT lot_size FROM instrument_master WHERE instrument_token=$1", token
            )
            lot_size = int(im_lot_row["lot_size"]) if im_lot_row and im_lot_row["lot_size"] else 1
            
            # Determine effective limit price for queuing:
            # - LIMIT: limit_price is the fill target
            # - SLM: trigger_price is the effective limit (market fill on trigger)
            # - SLL: limit_price is the fill target (once trigger hit)
            if ord_type == "LIMIT":
                effective_limit = Decimal(str(lp))
                effective_trigger = Decimal("0")
                order_type_str = "LIMIT"
            else:  # SLM / SLL
                effective_limit = Decimal(str(lp or body.trigger_price)) if ord_type == "SLL" else Decimal(str(body.trigger_price))
                effective_trigger = Decimal(str(body.trigger_price))
                order_type_str = "SL"
            
            queued = QueuedOrder(
                order_id         = order_id,
                user_id          = str(user_id),
                instrument_token = int(token or 0),
                side             = side,
                order_type       = order_type_str,
                exchange_segment = body.exchange_segment or "",
                symbol           = body.symbol or "",
                limit_price      = effective_limit,
                trigger_price    = effective_trigger,
                quantity         = qty,
                tick_size        = tick_size,
                lot_size         = lot_size,
            )
            await _queue_enqueue(queued)
            if ord_type == "LIMIT":
                log.info("LIMIT order %s queued: %s %s %s limit=%.2f",
                         order_id, side, qty, body.symbol, float(lp))
                return {"order_id": order_id, "status": "PENDING", "limit_price": float(lp)}
            else:
                log.info("SL order %s queued: %s %s %s trigger=%.2f",
                         order_id, side, qty, body.symbol, body.trigger_price)
                return {"order_id": order_id, "status": "PENDING", "trigger_price": body.trigger_price}

        # For partial MARKET fills, keep remaining quantity live in queue so
        # execution can continue immediately on subsequent depth updates.
        if ord_type == "MARKET" and market_status == "PARTIAL" and market_filled_qty < qty:
            from app.execution_simulator.execution_config import get_tick_size as _get_tick_size
            tick_size = _get_tick_size(body.exchange_segment or "NSE_FNO")
            im_lot_row = await pool.fetchrow(
                "SELECT lot_size FROM instrument_master WHERE instrument_token=$1", token
            )
            lot_size = int(im_lot_row["lot_size"]) if im_lot_row and im_lot_row["lot_size"] else 1
            remaining_qty = int(max(0, qty - market_filled_qty))
            queue_price = Decimal("99999999") if side == "BUY" else Decimal("0")
            queued = QueuedOrder(
                order_id=order_id,
                user_id=str(user_id),
                instrument_token=int(token or 0),
                side=side,
                order_type="MARKET",
                exchange_segment=body.exchange_segment or "",
                symbol=body.symbol or "",
                limit_price=queue_price,
                trigger_price=Decimal("0"),
                quantity=qty,
                tick_size=tick_size,
                lot_size=lot_size,
            )
            queued.remaining_qty = remaining_qty
            await _queue_enqueue(queued)
            log.info(
                "MARKET order %s queued remainder: %s %s %s rem=%s",
                order_id, side, qty, body.symbol, remaining_qty,
            )

        if ord_type == "MARKET":
            return {
                "order_id": order_id,
                "status": market_status,
                "fill_price": fill_price,
                "filled_qty": market_filled_qty,
                "requested_qty": qty,
            }

        return {"order_id": order_id, "status": "FILLED",
                "fill_price": fill_price, "filled_qty": qty}
    
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log the actual exception with full traceback
        log.error(f"CRITICAL ORDER PLACEMENT ERROR - User: {current_user.id}, Symbol: {body.symbol}", exc_info=True)
        log.error(f"Exception type: {type(e).__name__}, Message: {str(e)}")

        # Best-effort audit row so failed attempts are visible in Orders tab.
        try:
            pool = get_pool()
            fail_order_id = str(uuid.uuid4())
            fail_user_id = body.user_id or current_user.id
            fail_token = body.security_id or body.instrument_token or 0
            fail_side = (body.transaction_type or body.side or "BUY").upper()
            fail_type = (body.order_type or "MARKET").upper()
            fail_prod = (body.product_type or "MIS").upper()
            fail_qty = int(body.quantity or 0)
            await pool.execute(
                """
                INSERT INTO paper_orders
                    (order_id, user_id, instrument_token, symbol, exchange_segment,
                     side, order_type, quantity, status, product_type, placed_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'REJECTED', $9, NOW())
                """,
                fail_order_id,
                fail_user_id,
                int(fail_token or 0),
                body.symbol,
                body.exchange_segment,
                fail_side,
                fail_type,
                max(0, fail_qty),
                fail_prod,
            )
        except Exception as audit_exc:
            log.warning("Failed to write rejected audit order row: %s", audit_exc)

        raise HTTPException(
            status_code=500,
            detail=f"Order placement failed: {type(e).__name__}: {str(e)}"
        )


@router.get("")
async def list_orders(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    user_id:              Optional[str]  = Query(None),
    current_session_only: bool           = Query(False),
    status_filter:        Optional[str]  = Query(None),
    from_date:            Optional[str]  = Query(None),
    to_date:              Optional[str]  = Query(None),
):
    """
    List all orders placed during the day.
    Shows pending, executed, rejected, and cancelled orders.
    Admin/Super Admin can see all users' orders; regular users see their own.
    """
    from datetime import datetime
    
    uid   = user_id or current_user.id
    pool  = get_pool()
    
    # Non-admin users can only see their own orders
    if current_user.role not in ("ADMIN", "SUPER_ADMIN") and user_id and str(user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="You can only view your own orders")
    
    q     = "SELECT * FROM paper_orders WHERE user_id=$1 AND archived_at IS NULL"
    args  = [uid]
    
    # Filter by status if provided
    if status_filter:
        q += f" AND status=${len(args)+1}"
        args.append(status_filter.upper())
    
    # Filter by date range if provided.
    # Keep ACTIVE orders visible regardless of date so old pending/open orders
    # are not hidden from users when UI requests only today's orders.
    if from_date or to_date:
        date_parts = []
        if from_date:
            date_parts.append(f"DATE(placed_at AT TIME ZONE 'Asia/Kolkata') >= ${len(args)+1}")
            args.append(datetime.strptime(from_date, '%Y-%m-%d').date())
        if to_date:
            date_parts.append(f"DATE(placed_at AT TIME ZONE 'Asia/Kolkata') <= ${len(args)+1}")
            args.append(datetime.strptime(to_date, '%Y-%m-%d').date())

        date_clause = " AND ".join(date_parts) if date_parts else "TRUE"
        q += (
            " AND ("
            "status::text IN ('PENDING','OPEN','PARTIAL','PARTIAL_FILL','PARTIALLY_FILLED')"
            f" OR ({date_clause})"
            ")"
        )
    
    # Filter to only current session if requested
    if current_session_only:
        q += " AND placed_at >= CURRENT_DATE"
    
    q += " ORDER BY placed_at DESC LIMIT 500"
    rows = await pool.fetch(q, *args)
    return {"data": [_fmt(r) for r in rows]}


@router.get("/executed")
async def get_executed_trades(
    current_user: CurrentUser = Depends(get_current_user),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
):
    """
    Get executed (FILLED) trades for the authenticated user.
    Regular users can only see their own trades.
    Admin/Super Admin can optionally specify user_id to see other users' trades.
    
    All users can access this endpoint for viewing their own executed trades.
    """
    from datetime import datetime
    
    pool = get_pool()
    
    # Determine which user's trades to fetch
    target_uid = user_id
    
    # Non-admin users can only see their own trades
    if current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        if user_id and str(user_id) != str(current_user.id):
            raise HTTPException(status_code=403, detail="You can only view your own executed trades")
        target_uid = current_user.id
    
    # If no user_id specified and user is admin, default to their own trades
    if not target_uid:
        target_uid = current_user.id
    
    # Build query - filter by FILLED status (executed trades)
    q = "SELECT * FROM paper_orders WHERE user_id = $1 AND status = 'FILLED'"
    args = [target_uid]
    
    # Date range filtering (convert strings to date objects)
    if from_date:
        q += f" AND DATE(placed_at AT TIME ZONE 'Asia/Kolkata') >= ${len(args)+1}"
        args.append(datetime.strptime(from_date, '%Y-%m-%d').date())
    if to_date:
        q += f" AND DATE(placed_at AT TIME ZONE 'Asia/Kolkata') <= ${len(args)+1}"
        args.append(datetime.strptime(to_date, '%Y-%m-%d').date())
    
    q += " ORDER BY placed_at DESC LIMIT 1000"
    
    rows = await pool.fetch(q, *args)
    return {"data": [_fmt(r) for r in rows]}


@router.get("/historic/orders")
async def get_historic_orders(
    current_user: CurrentUser = Depends(get_current_user),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    mobile: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
):
    """
    Get historic executed trades for admin/super admin.
    Allows filtering by date range and user ID or mobile number.
    Returns all FILLED (executed) trades.
    
    Only ADMIN and SUPER_ADMIN roles can access this.
    """
    from datetime import datetime
    
    pool = get_pool()
    
    # Check if user is admin
    if current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(
            status_code=403,
            detail="Only admins can access historic trades."
        )
    
    # Build query - filter by FILLED status (executed trades)
    # Join with users table to get user_no for display
    q = """
        SELECT o.*, u.user_no 
        FROM paper_orders o 
        LEFT JOIN users u ON o.user_id = u.id 
        WHERE o.status = 'FILLED'
    """
    args = []
    
    # Date range filtering (convert strings to date objects)
    if from_date:
        q += f" AND DATE(o.placed_at AT TIME ZONE 'Asia/Kolkata') >= ${len(args)+1}"
        args.append(datetime.strptime(from_date, '%Y-%m-%d').date())
    if to_date:
        q += f" AND DATE(o.placed_at AT TIME ZONE 'Asia/Kolkata') <= ${len(args)+1}"
        args.append(datetime.strptime(to_date, '%Y-%m-%d').date())
    
    # User filtering
    if user_id:
        q += f" AND o.user_id = ${len(args)+1}::uuid"
        args.append(user_id)
    elif mobile:
        # Resolve mobile to user_id first to avoid join/cast edge cases that can cause 500s
        mobile_user_id = await pool.fetchval(
            "SELECT id FROM users WHERE mobile = $1 LIMIT 1",
            mobile,
        )
        if not mobile_user_id:
            return {"data": []}
        q += f" AND o.user_id = ${len(args)+1}::uuid"
        args.append(mobile_user_id)
    
    # Status filtering (optional, defaults to FILLED)
    if status_filter and status_filter.upper() != 'FILLED':
        q += f" AND o.status = ${len(args)+1}"
        args.append(status_filter.upper())
    
    q += " ORDER BY o.placed_at DESC LIMIT 1000"
    
    rows = await pool.fetch(q, *args)
    return {"data": [_fmt(r) for r in rows]}


@router.get("/{order_id}")
async def get_order(
    order_id: str = Path(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    pool = get_pool()
    row  = await pool.fetchrow("SELECT * FROM paper_orders WHERE order_id=$1", order_id)
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Authorization: Users can only view their own orders (except admins)
    if current_user.role not in ("ADMIN", "SUPER_ADMIN") and str(row["user_id"]) != str(current_user.id):
        raise HTTPException(status_code=403, detail="You can only view your own orders")
    
    return _fmt(row)


@router.delete("/{order_id}")
async def cancel_order(
    order_id: str = Path(...),
    current_user: CurrentUser = Depends(get_current_user),
):
    pool = get_pool()
    
    # First check if order exists and belongs to the user
    row = await pool.fetchrow(
        "SELECT user_id, status FROM paper_orders WHERE order_id=$1",
        order_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Order not found")
    
    # Authorization: Users can only cancel their own orders (except admins)
    if current_user.role not in ("ADMIN", "SUPER_ADMIN") and str(row["user_id"]) != str(current_user.id):
        raise HTTPException(status_code=403, detail="You can only cancel your own orders")
    
    # Check if order can be cancelled
    cancelable_statuses = {"PENDING", "PARTIAL", "PARTIAL_FILL", "PARTIALLY_FILLED"}
    if str(row["status"] or "").upper() not in cancelable_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel order with status {row['status']}."
        )
    
    # Delegate to execution engine — removes from in-memory queue AND updates DB
    from app.execution_simulator.execution_engine import cancel_order as _engine_cancel
    owner_id = str(row["user_id"])
    result = await _engine_cancel(order_id, owner_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("reason", "Cancel failed"))
    return {"success": True, "order_id": order_id}


def _fmt(r) -> dict:
    d = dict(r)
    d["id"]               = str(d.get("order_id", ""))
    d["transaction_type"] = d.get("transaction_type") or d.get("side", "")
    d["product_type"]     = d.get("product_type") or "MIS"
    d["security_id"]      = d.get("security_id") or d.get("instrument_token")
    d["price"]            = float(d.get("fill_price") or d.get("limit_price") or 0)
    d["status"]           = d.get("status", "PENDING")
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif hasattr(v, "__class__") and v.__class__.__name__ == "Decimal":
            d[k] = float(v)
    return d
