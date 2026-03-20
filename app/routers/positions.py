"""
app/routers/positions.py  (v2 — /portfolio/positions prefix)
"""
import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.database import get_pool
from app.dependencies import CurrentUser, get_current_user
from app.market_hours import is_market_open, get_market_state

log = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio/positions", tags=["Positions"])


def _uid(request: Request, user_id_param, current_user: Optional[CurrentUser] = None) -> str:
    if user_id_param:
        return str(user_id_param)
    hdr = request.headers.get("X-USER")
    if hdr:
        return hdr
    if current_user:
        return str(current_user.id)
    raise HTTPException(status_code=401, detail="Authentication required")


def _fmt(r) -> dict:
    d = dict(r)
    d["id"]           = str(d.get("position_id") or d.get("id") or d.get("instrument_token", ""))
    d["product_type"] = d.get("product_type") or "MIS"
    d["quantity"]     = int(d.get("quantity") or d.get("net_qty") or 0)
    d["avg_price"]    = float(d.get("avg_price") or d.get("avg_cost") or 0)
    d["mtm"]          = float(d.get("mtm") or d.get("unrealized_pnl") or 0)
    d["status"]       = d.get("status") or ("OPEN" if d["quantity"] != 0 else "CLOSED")
    d["realized_pnl"] = float(d.get("realized_pnl") or 0)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif hasattr(v, "__class__") and v.__class__.__name__ == "Decimal":
            d[k] = float(v)
    return d


@router.get("")
@router.get("/")
async def get_positions(
    request:  Request,
    current_user: CurrentUser = Depends(get_current_user),
    user_id:  Optional[str] = Query(None),
):
    """
    Returns today's intraday positions (open + today's closed).
    Overnight NORMAL equity holdings (opened on a previous day) are excluded here
    — those live on the Portfolio page instead.
    """
    uid  = _uid(request, user_id, current_user)
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT pp.*,
               COALESCE(NULLIF(im.lot_size, 0), 1) AS lot_size,
               COALESCE(md.ltp, pp.avg_price) AS ltp,
               COALESCE((md.ltp - pp.avg_price) * pp.quantity, 0) AS mtm
        FROM paper_positions pp
        LEFT JOIN instrument_master im ON im.instrument_token = pp.instrument_token
        LEFT JOIN market_data md ON md.instrument_token = pp.instrument_token
        WHERE pp.user_id = $1
          AND (
              pp.status = 'OPEN'
              OR (pp.status = 'CLOSED' AND pp.archived_at IS NULL)
          )
          -- Exclude overnight NORMAL equity positions (Portfolio page handles those)
          AND NOT (
              pp.status = 'OPEN'
              AND pp.quantity > 0
              AND pp.product_type = 'NORMAL'
              AND (pp.exchange_segment ILIKE '%\_EQ%' OR pp.exchange_segment IN ('NSE', 'BSE'))
              AND DATE(pp.opened_at AT TIME ZONE 'Asia/Kolkata') < CURRENT_DATE
          )
        ORDER BY pp.opened_at DESC
        """,
        uid,
    )
    return {"data": [_fmt(r) for r in rows]}


@router.get("/equity-holdings")
@router.get("/equity-holdings/")
async def get_equity_holdings(
    request:  Request,
    current_user: CurrentUser = Depends(get_current_user),
    user_id:  Optional[str] = Query(None),
):
    """
    Returns OPEN NORMAL equity positions that were opened on a previous trading day
    (i.e., overnight / delivery holdings).  These appear exclusively on the Portfolio
    page, not the regular Positions page.
    """
    uid  = _uid(request, user_id, current_user)
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT pp.*,
               COALESCE(NULLIF(im.lot_size, 0), 1) AS lot_size,
               COALESCE(md.ltp, pp.avg_price)                          AS ltp,
               COALESCE((md.ltp - pp.avg_price) * pp.quantity, 0)      AS mtm,
               (pp.quantity * pp.avg_price)                            AS invested_value,
               (pp.quantity * COALESCE(md.ltp, pp.avg_price))          AS current_value
        FROM paper_positions pp
        LEFT JOIN instrument_master im ON im.instrument_token = pp.instrument_token
        LEFT JOIN market_data md ON md.instrument_token = pp.instrument_token
        WHERE pp.user_id = $1::uuid
          AND pp.status   = 'OPEN'
          AND pp.quantity > 0
          AND pp.product_type = 'NORMAL'
          AND (pp.exchange_segment ILIKE '%\_EQ%' OR pp.exchange_segment IN ('NSE', 'BSE'))
          AND DATE(pp.opened_at AT TIME ZONE 'Asia/Kolkata') < CURRENT_DATE
        ORDER BY pp.opened_at DESC
        """,
        uid,
    )
    return {"data": [_fmt(r) for r in rows]}


@router.post("/{position_id}/close")
async def close_position(
    request:     Request,
    position_id: str = Path(...),
    current_user: CurrentUser = Depends(get_current_user),
    user_id:     Optional[str] = Query(None),  # admin can pass target user_id
):
    """Mark a specific position as closed (paper mode flat). Logs order for audit trail."""
    try:
        uid  = _uid(request, user_id, current_user)
        pool = get_pool()

        # BLOCKED users cannot exit positions
        _status_row = await pool.fetchrow(
            "SELECT status FROM users WHERE id=$1::uuid", uid
        )
        if _status_row and _status_row["status"] == "BLOCKED":
            raise HTTPException(
                status_code=403,
                detail="Your account is blocked. You can only submit payout requests.",
            )

        token = None
        resolved_position_id = None

        # Accept UUID position_id (preferred) and integer instrument_token (legacy).
        try:
            parsed_uuid = uuid.UUID(str(position_id))
            row = await pool.fetchrow(
                """
                SELECT *
                FROM paper_positions
                WHERE user_id=$1::uuid
                  AND position_id=$2::uuid
                LIMIT 1
                """,
                uid,
                parsed_uuid,
            )
        except (ValueError, TypeError):
            try:
                token = int(position_id)
            except (ValueError, TypeError):
                log.error(f"Invalid position_id format: {position_id}")
                raise HTTPException(status_code=400, detail="Invalid position ID format")

            # If multiple historical rows exist for same token, always target current OPEN row.
            row = await pool.fetchrow(
                """
                SELECT *
                FROM paper_positions
                WHERE user_id=$1::uuid
                  AND instrument_token=$2
                  AND status='OPEN'
                  AND quantity <> 0
                ORDER BY opened_at DESC
                LIMIT 1
                """,
                uid,
                token,
            )

        if not row:
            log.warning(f"Close position failed: Position {position_id} not found/open for user {uid}")
            raise HTTPException(status_code=404, detail="Position not found")

        resolved_position_id = row.get("position_id")

        # Extract position details
        exchange_segment = row.get("exchange_segment") or "NSE_EQ"
        symbol = row.get("symbol") or ""
        instrument_token = row["instrument_token"]
        qty = int(row.get("quantity") or row.get("net_qty") or 0)
        avg = float(row.get("avg_price") or row.get("avg_cost") or 0)
        product_type = row.get("product_type") or "MIS"
        
        if qty == 0:
            log.warning(f"Close position failed: Position {position_id} already has zero quantity")
            raise HTTPException(status_code=400, detail="Position already closed or has zero quantity")
        
        # For short positions, qty is negative. Use absolute value for order quantity.
        order_qty = abs(qty)
        side = 'SELL' if qty > 0 else 'BUY'
        
        # Get current LTP for fill price
        ltp_row = await pool.fetchrow(
            "SELECT ltp FROM market_data WHERE instrument_token=$1",
            instrument_token,
        )
        ltp = float(ltp_row["ltp"]) if (ltp_row and ltp_row["ltp"]) else avg
        
        # Generate order ID
        order_id = str(uuid.uuid4())
        
        # ── CRITICAL: Log the exit order FIRST (audit trail) ──────────────────
        # This ensures ALL exit attempts are recorded, even if rejected
        await pool.execute(
            """
            INSERT INTO paper_orders
                (order_id, user_id, instrument_token, symbol, exchange_segment,
                 side, order_type, quantity, fill_price, filled_qty,
                 status, product_type, placed_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'MARKET', $7, $8, 0, 'PENDING', $9, NOW())
            """,
            order_id, uid, instrument_token, symbol, exchange_segment,
            side, order_qty, ltp, product_type
        )
        
        # ── Market hours validation ────────────────────────────────────────
        if not is_market_open(exchange_segment, symbol):
            market_state = get_market_state(exchange_segment, symbol)
            
            # Update order status to REJECTED
            await pool.execute(
                "UPDATE paper_orders SET status = 'REJECTED' WHERE order_id = $1",
                order_id
            )
            
            log.warning(f"Close position rejected: Market is {market_state.value}")
            raise HTTPException(
                status_code=403,
                detail=f"Market is {market_state.value}. Positions can only be closed during market hours."
            )
        
        # Calculate realized P&L
        realized = round((ltp - avg) * qty, 2)

        # Update order to FILLED and close position
        await pool.execute(
            """
            UPDATE paper_orders 
            SET status = 'FILLED', filled_qty = $2, filled_at = NOW()
            WHERE order_id = $1
            """,
            order_id, order_qty
        )
        
        # Close the exact resolved row to avoid touching wrong historical entries.
        await pool.execute(
            """
            UPDATE paper_positions
            SET quantity = 0, status = 'CLOSED', realized_pnl = $1, closed_at = NOW()
            WHERE user_id = $2::uuid AND position_id = $3::uuid
            """,
            realized, uid, resolved_position_id,
        )
        
        log.info(f"Position closed successfully: token={instrument_token}, user={uid}, realized_pnl={realized}")
        return {"success": True, "realized_pnl": realized, "order_id": order_id}
    
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"CRITICAL CLOSE POSITION ERROR - User: {current_user.id}, Position: {position_id}", exc_info=True)
        log.error(f"Exception type: {type(e).__name__}, Message: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Position close failed: {type(e).__name__}: {str(e)}"
        )


@router.get("/pnl/summary")
@router.get("/pnl/summary/")
async def pnl_summary(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    user_id: Optional[str] = Query(None),
):
    uid  = _uid(request, user_id, current_user)
    pool = get_pool()
    row  = await pool.fetchrow(
        """
        SELECT
            COALESCE(SUM(CASE WHEN quantity != 0
                         THEN (COALESCE(md.ltp, pp.avg_price) - pp.avg_price) * pp.quantity
                         ELSE 0 END), 0)                AS unrealized_pnl,
            COALESCE(SUM(COALESCE(realized_pnl,0)),0)   AS realized_pnl,
            COALESCE(SUM(COALESCE(total_charges,0)),0)  AS total_charges,
            COALESCE(SUM(COALESCE(brokerage_charge,0)),0) AS brokerage_charge,
            COALESCE(SUM(COALESCE(stt_ctt_charge,0)),0)   AS stt_ctt_charge,
            COALESCE(SUM(COALESCE(exchange_charge,0)),0)  AS exchange_charge,
            COALESCE(SUM(COALESCE(sebi_charge,0)),0)      AS sebi_charge,
            COALESCE(SUM(COALESCE(stamp_duty,0)),0)       AS stamp_duty,
            COALESCE(SUM(COALESCE(ipft_charge,0)),0)      AS ipft_charge,
            COALESCE(SUM(COALESCE(gst_charge,0)),0)       AS gst_charge,
            COALESCE(SUM(COALESCE(platform_cost,0)),0)    AS platform_cost,
            COALESCE(SUM(COALESCE(trade_expense,0)),0)    AS trade_expense
        FROM paper_positions pp
        LEFT JOIN market_data md ON md.instrument_token = pp.instrument_token
        WHERE pp.user_id = $1
        """,
        uid,
    )
    unr = float(row["unrealized_pnl"])
    rea = float(row["realized_pnl"])
    charges = float(row["total_charges"])
    net_realized = rea - charges
    
    return {
        "unrealized_pnl": unr,
        "realized_pnl": rea,
        "total_charges": charges,
        "net_realized_pnl": net_realized,
        "total_pnl": unr + net_realized,
        "charge_breakdown": {
            "brokerage": float(row["brokerage_charge"]),
            "stt_ctt": float(row["stt_ctt_charge"]),
            "exchange": float(row["exchange_charge"]),
            "sebi": float(row["sebi_charge"]),
            "stamp_duty": float(row["stamp_duty"]),
            "ipft": float(row["ipft_charge"]),
            "gst": float(row["gst_charge"]),
            "platform_cost": float(row["platform_cost"]),
            "trade_expense": float(row["trade_expense"]),
        }
    }


@router.get("/pnl/historic")
@router.get("/pnl/historic/")
async def pnl_historic(
    request:   Request,
    current_user: CurrentUser = Depends(get_current_user),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD (IST), defaults to today"),
    to_date:   Optional[str] = Query(None, description="YYYY-MM-DD (IST), defaults to today"),
    user_id:   Optional[str] = Query(None),
):
    """
    Returns realized P&L from CLOSED positions within the date range,
    plus unrealized MTM for currently OPEN positions.
    Date range is inclusive, anchored to IST (UTC+5:30).
    """
    uid  = _uid(request, user_id, current_user)
    pool = get_pool()

    IST = timezone(timedelta(hours=5, minutes=30))
    today_ist = datetime.now(IST).date()

    try:
        fd = date.fromisoformat(from_date) if from_date else today_ist
        td = date.fromisoformat(to_date)   if to_date   else today_ist
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

    # Convert IST calendar dates → UTC timestamps for DB comparison
    from_utc = datetime(fd.year, fd.month, fd.day, 0, 0, 0, tzinfo=IST)
    to_utc   = datetime(td.year, td.month, td.day, 23, 59, 59, 999999, tzinfo=IST)

    # Backfill missing charges for this user/date-range before fetching report rows
    pending_count = await pool.fetchval(
        """
        SELECT COUNT(*)
        FROM paper_positions pp
        WHERE pp.user_id = $1::uuid
          AND pp.status = 'CLOSED'
          AND pp.closed_at IS NOT NULL
          AND pp.closed_at >= $2
          AND pp.closed_at <= $3
          AND COALESCE(pp.charges_calculated, FALSE) = FALSE
        """,
        uid, from_utc, to_utc,
    )
    if int(pending_count or 0) > 0:
        try:
            from app.schedulers.charge_calculation_scheduler import charge_calculation_scheduler
            result = await charge_calculation_scheduler.run_once(
                exchanges=None,
                user_id=str(uid),
                closed_from=from_utc,
                closed_to=to_utc,
            )
            log.info(f"✅ Charge backfill for user {uid}: {result['processed']} processed, {result['errors']} errors")
        except Exception as e:
            # Do not fail P&L response if backfill fails; report will still return available data
            # But DO log the error for debugging purposes
            log.warning(f"⚠️ Charge backfill FAILED for user {uid}: {str(e)}")
            import traceback
            log.debug(f"Exception traceback: {traceback.format_exc()}")

    # ── Closed positions within range ──────────────────────────────────
    closed_rows = await pool.fetch(
        """
        SELECT
            pp.position_id,
            pp.instrument_token,
            pp.symbol,
            pp.exchange_segment,
            pp.product_type,
            pp.quantity          AS closed_qty,
            pp.avg_price         AS entry_price,
            pp.realized_pnl,
            pp.total_charges,
            pp.charges_calculated,
            pp.brokerage_charge,
            pp.stt_ctt_charge,
            pp.exchange_charge,
            pp.sebi_charge,
            pp.stamp_duty,
            pp.ipft_charge,
            pp.gst_charge,
            pp.platform_cost,
            pp.trade_expense,
            (pp.realized_pnl - COALESCE(pp.total_charges, 0)) AS net_pnl,
            pp.closed_at::date AS report_date,
            COALESCE(os.buy_qty, 0) AS buy_qty,
            COALESCE(os.buy_value, 0) AS buy_value,
            CASE WHEN COALESCE(os.buy_qty, 0) > 0 THEN ROUND(os.buy_value / os.buy_qty, 2) ELSE 0 END AS buy_price,
            COALESCE(os.sell_qty, 0) AS sell_qty,
            COALESCE(os.sell_value, 0) AS sell_value,
            CASE WHEN COALESCE(os.sell_qty, 0) > 0 THEN ROUND(os.sell_value / os.sell_qty, 2) ELSE 0 END AS sell_price,
            pp.opened_at,
            pp.closed_at
        FROM paper_positions pp
        LEFT JOIN LATERAL (
            SELECT
                COALESCE(SUM(CASE WHEN po.side = 'BUY'  THEN COALESCE(po.filled_qty, 0) ELSE 0 END), 0) AS buy_qty,
                COALESCE(SUM(CASE WHEN po.side = 'BUY'  THEN COALESCE(po.filled_qty, 0) * COALESCE(po.fill_price, 0) ELSE 0 END), 0) AS buy_value,
                COALESCE(SUM(CASE WHEN po.side = 'SELL' THEN COALESCE(po.filled_qty, 0) ELSE 0 END), 0) AS sell_qty,
                COALESCE(SUM(CASE WHEN po.side = 'SELL' THEN COALESCE(po.filled_qty, 0) * COALESCE(po.fill_price, 0) ELSE 0 END), 0) AS sell_value
            FROM paper_orders po
            WHERE po.user_id = pp.user_id
              AND po.instrument_token = pp.instrument_token
              AND po.status = 'FILLED'
              AND po.placed_at >= pp.opened_at
              AND po.placed_at <= pp.closed_at
        ) os ON TRUE
        WHERE pp.user_id = $1::uuid
          AND pp.status  = 'CLOSED'
          AND pp.closed_at >= $2
          AND pp.closed_at <= $3
        ORDER BY pp.closed_at DESC
        """,
        uid, from_utc, to_utc,
    )

    # ── Open positions (unrealized) ─────────────────────────────────────
    open_rows = await pool.fetch(
        """
        SELECT
            pp.instrument_token,
            pp.symbol,
            pp.exchange_segment,
            pp.product_type,
            pp.quantity,
            pp.avg_price,
            COALESCE(md.ltp, pp.avg_price)                        AS ltp,
            COALESCE((md.ltp - pp.avg_price) * pp.quantity, 0)   AS mtm,
            pp.opened_at
        FROM paper_positions pp
        LEFT JOIN market_data md ON md.instrument_token = pp.instrument_token
        WHERE pp.user_id = $1::uuid
          AND pp.status  = 'OPEN'
          AND pp.quantity != 0
        ORDER BY pp.opened_at DESC
        """,
        uid,
    )

    def _to_dict(r, extra=None):
        d = {}
        for k in r.keys():
            v = r[k]
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
            elif hasattr(v, "__class__") and v.__class__.__name__ == "Decimal":
                d[k] = float(v)
            else:
                d[k] = v
        if extra:
            d.update(extra)
        return d

    closed_list = [_to_dict(r) for r in closed_rows]
    open_list   = [_to_dict(r) for r in open_rows]

    # Calculate totals including charges
    realized_total   = sum(float(r.get("realized_pnl") or 0) for r in closed_list)
    charges_total    = sum(float(r.get("total_charges") or 0) for r in closed_list)
    net_realized     = realized_total - charges_total
    unrealized_total = sum(float(r.get("mtm") or 0) for r in open_list)

    return {
        "data": {
            "closed":           closed_list,
            "open":             open_list,
            "realized_pnl":     round(realized_total,   2),
            "total_charges":    round(charges_total,    2),
            "net_realized_pnl": round(net_realized,     2),
            "unrealized_pnl":   round(unrealized_total, 2),
            "net_pnl":          round(net_realized + unrealized_total, 2),
            "closed_count":     len(closed_list),
            "open_count":       len(open_list),
            "from_date":        fd.isoformat(),
            "to_date":          td.isoformat(),
        }
    }
