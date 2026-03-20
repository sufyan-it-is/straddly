"""
app/execution_simulator/execution_engine.py
=============================================
Core execution engine — routes orders to fill/queue/reject logic.
Persists results to paper_orders, paper_positions, paper_trades, execution_log.
Called by:
  - API router (new order)
  - tick_processor (on every market tick — checks queued limit orders)
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.database                 import get_pool
from app.market_hours             import is_market_open
from .execution_config            import get_tick_size
from .rejection_engine            import check_rejection, RejectionCode
from .latency_model               import apply_latency
from .fill_engine                 import execute_market_fill, FillEvent
from .order_queue_manager         import (
    QueuedOrder, enqueue, cancel, cancel_by_id, get_fillable, remove_filled, pending_count,
)

log = logging.getLogger(__name__)

# Shared mock_mode flag — always True for this paper-trading platform.
# Kept as a flag so the Admin toggle still works; default changed to True.
_mock_mode: bool = True


def set_mock_mode(enabled: bool) -> None:
    global _mock_mode
    _mock_mode = enabled
    log.info(f"Execution engine mode: {'PAPER' if enabled else 'LIVE'}")


def is_mock_mode() -> bool:
    return _mock_mode


# ── Main entry point: place_order ─────────────────────────────────────────


async def place_order(order, user_id: str, market_snap: dict) -> dict:
    """
    Args:
        order:       Pydantic order-request model
        user_id:     authenticated user id
        market_snap: {'ltp', 'bid_depth', 'ask_depth', 'ltt', ...} from market_data

    Returns:
        dict with keys: order_id, status, rejection_code?, avg_price?, fills
    """
    if not _mock_mode:
        raise RuntimeError("Execution engine is not in PAPER mode — live trading not implemented.")

    order_id = str(uuid.uuid4())
    tick_size = get_tick_size(order.exchange_segment)

    # ── Fetch lot size from instrument master ─────────────────────────────
    pool     = get_pool()
    im_row   = await pool.fetchrow(
        "SELECT lot_size FROM instrument_master WHERE instrument_token=$1",
        order.instrument_token,
    )
    lot_size = int(im_row["lot_size"]) if im_row and im_row["lot_size"] else 1

    # ── Pre-flight rejection check (synchronous) ──────────────────────────
    rejection = check_rejection(order, market_snap, lot_size=lot_size)
    if rejection:
        await _log_execution(
            order_id, user_id, order, status="REJECTED",
            reason=rejection.value, latency_ms=0,
        )
        return {"order_id": order_id, "status": "REJECTED", "rejection_code": rejection.value}

    await apply_latency(order.exchange_segment)

    # ── MARKET order: fill immediately from current depth ─────────────────
    if order.order_type == "MARKET":
        fills = execute_market_fill(order, market_snap, tick_size, lot_size)
        avg_price, filled_qty = _avg_price_and_qty(fills)
        await _persist_fills(
            order_id, user_id, order, fills, avg_price, filled_qty, tick_size
        )
        status = "COMPLETE" if filled_qty == order.quantity else "PARTIALLY_FILLED"
        await _log_execution(
            order_id, user_id, order, status=status,
            avg_price=avg_price, latency_ms=0,
        )
        return {
            "order_id":  order_id,
            "status":    status,
            "avg_price": float(avg_price),
            "fills":     [{"price": float(f.fill_price), "qty": f.fill_qty} for f in fills],
        }

    # ── LIMIT / SL order: queue for deferred fill ─────────────────────────
    queued = QueuedOrder(
        order_id         = order_id,
        user_id          = user_id,
        instrument_token = order.instrument_token,
        side             = order.side,
        order_type       = order.order_type,
        exchange_segment = order.exchange_segment,
        symbol           = order.symbol,
        limit_price      = Decimal(str(order.limit_price)),
        trigger_price    = Decimal(str(getattr(order, "trigger_price", 0) or 0)),
        quantity         = order.quantity,
        tick_size        = tick_size,
        lot_size         = lot_size,
    )
    await _insert_paper_order(order_id, user_id, order, status="PENDING")
    await enqueue(queued)
    await _log_execution(
        order_id, user_id, order, status="PENDING", latency_ms=0
    )
    return {"order_id": order_id, "status": "PENDING"}


# ── Called by tick_processor on every tick flush ──────────────────────────


async def on_tick(instrument_token: int, market_snap: dict) -> None:
    """
    DISABLED: Continuous tick-based execution replaced by 30-second partial fill monitor.
    This prevents excessive execution attempts on every tick.
    Partial fills are now handled by app/execution_simulator/partial_fill_monitor.py
    """
    if not _mock_mode:
        return
    
    # DISABLED: return immediately without processing
    return

    ltp = market_snap.get("ltp")
    if not ltp:
        return

    market_price = Decimal(str(ltp))
    tick_size    = Decimal(str(market_snap.get("tick_size", "0.05")))
    bid_depth = market_snap.get("bid_depth") or []
    ask_depth = market_snap.get("ask_depth") or []

    def _to_decimal_price(level):
        try:
            if isinstance(level, dict) and level.get("price") is not None:
                return Decimal(str(level.get("price")))
        except Exception:
            return None
        return None

    best_bid = _to_decimal_price(bid_depth[0]) if bid_depth else None
    best_ask = _to_decimal_price(ask_depth[0]) if ask_depth else None

    for side in ("BUY", "SELL"):
        fillable = await get_fillable(
            instrument_token,
            side,
            market_price,
            best_bid=best_bid,
            best_ask=best_ask,
        )
        for queued in fillable:
            remaining_before = int(getattr(queued, "remaining_qty", queued.quantity))
            fills = execute_market_fill(
                queued, market_snap, tick_size, queued.lot_size
            )
            avg_price, filled_qty = _avg_price_and_qty(fills)
            if filled_qty == 0:
                continue

            # Keep queued order progress in memory so subsequent ticks continue
            # from remaining quantity instead of restarting from full quantity.
            queued.remaining_qty = max(0, remaining_before - filled_qty)

            await _persist_fills(
                queued.order_id, queued.user_id, queued, fills,
                avg_price, filled_qty, tick_size
            )

            # Remove from queue only when fully complete; partials stay queued
            # and wait for new depth on next ticks.
            if queued.remaining_qty == 0:
                await remove_filled(instrument_token, side, queued.limit_price, queued.order_id)


# ── Cancel an open order ──────────────────────────────────────────────────


async def cancel_order(order_id: str, user_id: str) -> dict:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM paper_orders WHERE order_id=$1 AND user_id=$2",
        order_id, user_id,
    )
    if not row:
        return {"success": False, "reason": "Order not found"}
    if str(row["status"] or "").upper() not in ("PENDING", "PARTIAL", "PARTIAL_FILL", "PARTIALLY_FILLED"):
        return {"success": False, "reason": f"Cannot cancel order in state {row['status']}"}

    # Use cancel_by_id so we don't need to know the exact price-level key.
    # This is important for SLM orders where the queued limit_price (= trigger_price)
    # differs from the limit_price column stored in the DB (which may be 0 / NULL).
    await cancel_by_id(order_id)
    
    # Clear depth snapshot for cancelled orders
    from app.execution_simulator.partial_fill_monitor import partial_fill_monitor
    partial_fill_monitor.clear_snapshot(order_id)
    
    row_data = dict(row)
    qty = int(row_data.get("quantity") or 0)
    filled_qty = int(row_data.get("filled_qty") or 0)
    rejected_qty = int(row_data.get("rejected_qty") or 0)
    pending_qty = max(0, qty - filled_qty - rejected_qty)

    # Backward-compatible cancellation update:
    # some environments may not yet have rejected_qty/remaining_qty columns.
    try:
        await pool.execute(
            """
            UPDATE paper_orders
            SET status='CANCELLED',
                remaining_qty = 0,
                rejected_qty = COALESCE(rejected_qty, 0) + $2,
                updated_at=now()
            WHERE order_id=$1
            """,
            order_id,
            pending_qty,
        )
    except Exception:
        try:
            await pool.execute(
                """
                UPDATE paper_orders
                SET status='CANCELLED',
                    remaining_qty = 0,
                    updated_at=now()
                WHERE order_id=$1
                """,
                order_id,
            )
        except Exception:
            await pool.execute(
                """
                UPDATE paper_orders
                SET status='CANCELLED',
                    updated_at=now()
                WHERE order_id=$1
                """,
                order_id,
            )
    return {"success": True}


# ── Startup reconciliation ────────────────────────────────────────────────


async def reconcile_pending_orders() -> int:
    """
    Called once at startup: re-enqueue any PENDING orders that already exist
    in the database (e.g. survive a container restart).
    Returns the number of orders re-queued.
    """
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT order_id, user_id, instrument_token, exchange_segment, symbol,
               side, order_type, quantity, remaining_qty, limit_price, trigger_price
        FROM   paper_orders
                WHERE  status IN ('PENDING', 'PARTIAL')
                    AND COALESCE(remaining_qty, 0) > 0
        """
    )
    if not rows:
        return 0

    from .execution_config import get_tick_size as _get_tick_size

    count = 0
    for row in rows:
        try:
            tick_size = _get_tick_size(row["exchange_segment"] or "NSE_FNO")
            # SLM effective limit is the trigger price (market fill on trigger)
            order_type = str(row["order_type"] or "").upper()
            if order_type == "MARKET":
                raw_limit = Decimal("99999999") if str(row["side"] or "").upper() == "BUY" else Decimal("0")
            else:
                raw_limit = Decimal(str(row["limit_price"] or 0))
            queued = QueuedOrder(
                order_id         = str(row["order_id"]),
                user_id          = str(row["user_id"]),
                instrument_token = int(row["instrument_token"]),
                side             = row["side"],
                order_type       = order_type,
                exchange_segment = row["exchange_segment"] or "",
                symbol           = row["symbol"] or "",
                limit_price      = raw_limit,
                trigger_price    = Decimal(str(row["trigger_price"] or 0)),
                quantity         = int(row["quantity"]),
                tick_size        = tick_size,
            )
            queued.remaining_qty = int(row["remaining_qty"] or row["quantity"])
            await enqueue(queued)
            count += 1
        except Exception as exc:
            log.warning("reconcile_pending_orders: skipped order %s — %s", row["order_id"], exc)

    log.info("reconcile_pending_orders: re-queued %d PENDING orders", count)
    return count


# ── Internal helpers ──────────────────────────────────────────────────────


def _avg_price_and_qty(fills: list[FillEvent]) -> tuple[Decimal, int]:
    total_cost = sum(f.fill_price * f.fill_qty for f in fills)
    total_qty  = sum(f.fill_qty for f in fills)
    if total_qty == 0:
        return Decimal("0"), 0
    return (total_cost / total_qty), total_qty


async def _insert_paper_order(order_id, user_id, order, status):
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO paper_orders
            (order_id, user_id, instrument_token, exchange_segment, symbol,
             order_type, side, quantity, remaining_qty, limit_price, trigger_price, status)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$8,$9,$10,$11)
        """,
        order_id, user_id, order.instrument_token, order.exchange_segment,
        order.symbol, order.order_type, order.side, order.quantity,
        getattr(order, "limit_price", 0) or 0,
        getattr(order, "trigger_price", 0) or 0,
        status,
    )


async def _persist_fills(
    order_id, user_id, order, fills: list[FillEvent],
    avg_price: Decimal, filled_qty: int, tick_size: Decimal
):
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT quantity, filled_qty, rejected_qty FROM paper_orders WHERE order_id=$1",
            order_id,
        )
        total_qty = int((existing["quantity"] if existing else getattr(order, "quantity", 0)) or 0)
        prev_filled = int((existing["filled_qty"] if existing else 0) or 0)
        prev_rejected = int((existing["rejected_qty"] if existing else 0) or 0)
        new_filled_total = min(total_qty, prev_filled + int(filled_qty or 0))

        remaining_after = getattr(order, "remaining_qty", None)
        if remaining_after is None:
            remaining_after = max(0, total_qty - new_filled_total - prev_rejected)
        else:
            remaining_after = max(0, int(remaining_after))

        status_value = "FILLED" if remaining_after == 0 else "PARTIAL"

        await conn.execute(
            """
            INSERT INTO paper_orders
                (order_id, user_id, instrument_token, exchange_segment, symbol,
                 order_type, side, quantity, remaining_qty, limit_price,
                 trigger_price, avg_fill_price, fill_price, filled_qty, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (order_id) DO UPDATE SET
                avg_fill_price = EXCLUDED.avg_fill_price,
                fill_price     = EXCLUDED.fill_price,
                filled_qty     = EXCLUDED.filled_qty,
                remaining_qty  = EXCLUDED.remaining_qty,
                status         = EXCLUDED.status,
                updated_at     = now()
            """,
            order_id, user_id, order.instrument_token, order.exchange_segment,
            order.symbol, order.order_type, order.side, order.quantity,
            remaining_after,
            getattr(order, "limit_price", 0) or 0,
            getattr(order, "trigger_price", 0) or 0,
            avg_price,
            avg_price,
            new_filled_total,
            status_value,
        )
        
        # Clear depth snapshot if order fully filled
        if remaining_after == 0:
            # Clear snapshot for fully filled orders
            from app.execution_simulator.partial_fill_monitor import partial_fill_monitor
            partial_fill_monitor.clear_snapshot(order_id)

        # Trade records
        for fill in fills:
            if fill.fill_qty == 0:
                continue
            await conn.execute(
                """
                INSERT INTO paper_trades
                    (order_id, user_id, instrument_token, exchange_segment, symbol,
                     side, fill_qty, fill_price, slippage)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                """,
                order_id, user_id, order.instrument_token, order.exchange_segment,
                order.symbol, order.side, fill.fill_qty, fill.fill_price, fill.slippage,
            )

        # Update paper_positions
        await _update_position(conn, user_id, order, avg_price, filled_qty)


async def _update_position(conn, user_id, order, avg_price, filled_qty):
    """
    Update position after order fill.
    Fixed: allows multiple CLOSED positions for same instrument (same-day re-entry).
    """
    # Check for existing OPEN position only
    existing = await conn.fetchrow(
        """
        SELECT position_id, quantity, avg_price FROM paper_positions
        WHERE user_id=$1 AND instrument_token=$2 AND exchange_segment=$3 AND status='OPEN'
        """,
        user_id, order.instrument_token, order.exchange_segment,
    )
    
    if not existing:
        # No open position - create new one
        if filled_qty > 0:
            qty = filled_qty if order.side == "BUY" else -filled_qty
            await conn.execute(
                """
                INSERT INTO paper_positions
                    (user_id, instrument_token, exchange_segment, symbol,
                     quantity, avg_price, status)
                VALUES ($1,$2,$3,$4,$5,$6,'OPEN')
                """,
                user_id, order.instrument_token, order.exchange_segment,
                order.symbol, qty, avg_price,
            )
        return

    existing_qty = existing["quantity"]
    existing_avg = Decimal(str(existing["avg_price"]))
    position_id = existing["position_id"]
    
    if order.side == "BUY":
        # Add to existing position
        new_qty = existing_qty + filled_qty
        new_avg = (
            (existing_avg * existing_qty + avg_price * filled_qty) / new_qty
            if new_qty != 0 else Decimal("0")
        )
        await conn.execute(
            """
            UPDATE paper_positions
            SET quantity=$1, avg_price=$2, updated_at=now()
            WHERE position_id=$3
            """,
            new_qty, new_avg, position_id,
        )
    else:
        # Reduce or close existing position
        new_qty = existing_qty - filled_qty
        if new_qty <= 0:
            # Position fully closed
            realized_pnl = (avg_price - existing_avg) * existing_qty
            await conn.execute(
                """
                UPDATE paper_positions
                SET quantity=0, status='CLOSED', realized_pnl=$1, closed_at=now(), updated_at=now()
                WHERE position_id=$2
                """,
                float(realized_pnl), position_id,
            )
        else:
            # Partial close
            realized_pnl = (avg_price - existing_avg) * filled_qty
            await conn.execute(
                """
                UPDATE paper_positions
                SET quantity=$1, 
                    realized_pnl=COALESCE(realized_pnl, 0) + $2,
                    updated_at=now()
                WHERE position_id=$3
                """,
                new_qty, float(realized_pnl), position_id,
            )


async def _update_paper_order_status(order_id, status, avg_price):
    pool = get_pool()
    await pool.execute(
        "UPDATE paper_orders SET status=$1, avg_fill_price=$2, updated_at=now() WHERE order_id=$3",
        status, avg_price, order_id,
    )


async def _log_execution(order_id, user_id, order, status, reason=None, avg_price=None, latency_ms=0):
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO execution_log
            (order_id, user_id, instrument_token, event_type, status, reason,
             avg_price, latency_ms)
        VALUES ($1,$2,$3,'ORDER_PLACED',$4,$5,$6,$7)
        """,
        order_id, user_id, order.instrument_token, status, reason, avg_price, latency_ms,
    )
