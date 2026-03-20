"""
app/execution_simulator/super_order_monitor.py
===============================================
Background task monitoring Super Orders (Target + Stop-Loss + Trailing Stop).

Super Orders are advanced bracket orders with:
- Target Price: auto-exit when profit target hit
- Stop Loss Price: auto-exit when stop loss hit
- Trailing Jump: move stop loss up/down as position becomes profitable

This monitor runs every 1 second and checks all open positions with super orders.
"""
import asyncio
import logging
from decimal import Decimal
from typing import Optional

from app.database import get_pool

log = logging.getLogger(__name__)

_monitor_task: Optional[asyncio.Task] = None
_running = False


async def start_monitor() -> None:
    """Start the super order monitoring background task."""
    global _monitor_task, _running
    if _running:
        log.warning("Super order monitor already running.")
        return
    
    _running = True
    _monitor_task = asyncio.create_task(_monitor_loop(), name="super-order-monitor")
    log.info("Super order monitor started.")


async def stop_monitor() -> None:
    """Stop the super order monitoring background task."""
    global _monitor_task, _running
    if not _running or not _monitor_task:
        return
    
    _running = False
    _monitor_task.cancel()
    try:
        await _monitor_task
    except asyncio.CancelledError:
        pass
    log.info("Super order monitor stopped.")


async def _monitor_loop() -> None:
    """Main monitoring loop - checks super orders every second."""
    while _running:
        try:
            await asyncio.sleep(1.0)
            await _check_all_super_orders()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error(f"Super order monitor error: {exc}", exc_info=True)
            await asyncio.sleep(5.0)  # back off on errors


async def _check_all_super_orders() -> None:
    """
    Check all open positions that have associated super orders.
    Execute auto-exit if target or stop-loss hit.
    """
    pool = get_pool()
    
    # Find all open positions with active super orders
    rows = await pool.fetch(
        """
        SELECT 
            po.order_id,
            po.user_id,
            po.instrument_token,
            po.symbol,
            po.exchange_segment,
            po.side,
            po.quantity,
            po.product_type,
            po.target_price,
            po.stop_loss_price,
            po.trailing_jump,
            po.fill_price AS entry_price,
            pp.position_id,
            pp.quantity AS current_qty,
            pp.avg_price,
            md.ltp
        FROM paper_orders po
        INNER JOIN paper_positions pp 
            ON pp.user_id = po.user_id 
            AND pp.instrument_token = po.instrument_token
            AND pp.status = 'OPEN'
        LEFT JOIN market_data md ON md.instrument_token = po.instrument_token
        WHERE po.is_super = TRUE
          AND po.status = 'FILLED'
          AND pp.quantity != 0
        """
    )
    
    for row in rows:
        try:
            await _check_super_order(dict(row))
        except Exception as exc:
            log.error(
                f"Error checking super order {row['order_id']}: {exc}",
                exc_info=True
            )


async def _check_super_order(order: dict) -> None:
    """
    Check a single super order and execute exit if conditions met.
    
    Logic:
    - BUY position: exit when LTP >= target OR LTP <= stop_loss
    - SELL position: exit when LTP <= target OR LTP >= stop_loss
    - Trailing: adjust stop_loss as position becomes profitable
    """
    ltp = order.get("ltp")
    if not ltp or ltp <= 0:
        return
    
    ltp = float(ltp)
    side = order["side"]
    avg_price = float(order["avg_price"])
    target = float(order["target_price"]) if order["target_price"] else None
    stop_loss = float(order["stop_loss_price"]) if order["stop_loss_price"] else None
    trailing_jump = float(order["trailing_jump"]) if order["trailing_jump"] else None
    current_qty = int(order["current_qty"])
    
    if current_qty == 0:
        return  # Position already closed
    
    # Determine exit conditions
    should_exit = False
    exit_reason = None
    
    if side == "BUY":
        # Long position
        if target and ltp >= target:
            should_exit = True
            exit_reason = f"Target hit: LTP {ltp} >= Target {target}"
        elif stop_loss and ltp <= stop_loss:
            should_exit = True
            exit_reason = f"Stop-loss hit: LTP {ltp} <= SL {stop_loss}"
        
        # Trailing stop: move SL up as position profits
        if trailing_jump and not should_exit and stop_loss:
            profit = ltp - avg_price
            if profit > 0:
                # Calculate how many jumps we should have made
                jumps = int(profit // trailing_jump)
                if jumps > 0:
                    new_sl = stop_loss + (jumps * trailing_jump)
                    if new_sl > stop_loss:
                        # Update stop loss in database
                        await _update_stop_loss(order["order_id"], new_sl)
                        log.info(
                            f"Trailing SL updated for {order['symbol']}: "
                            f"{stop_loss:.2f} → {new_sl:.2f}"
                        )
    
    elif side == "SELL":
        # Short position
        if target and ltp <= target:
            should_exit = True
            exit_reason = f"Target hit: LTP {ltp} <= Target {target}"
        elif stop_loss and ltp >= stop_loss:
            should_exit = True
            exit_reason = f"Stop-loss hit: LTP {ltp} >= SL {stop_loss}"
        
        # Trailing stop: move SL down as position profits
        if trailing_jump and not should_exit and stop_loss:
            profit = avg_price - ltp
            if profit > 0:
                jumps = int(profit // trailing_jump)
                if jumps > 0:
                    new_sl = stop_loss - (jumps * trailing_jump)
                    if new_sl < stop_loss:
                        await _update_stop_loss(order["order_id"], new_sl)
                        log.info(
                            f"Trailing SL updated for {order['symbol']}: "
                            f"{stop_loss:.2f} → {new_sl:.2f}"
                        )
    
    if should_exit:
        await _execute_super_exit(order, ltp, exit_reason)


async def _execute_super_exit(order: dict, exit_price: float, reason: str) -> None:
    """
    Execute automatic exit for a super order.
    Places a MARKET order in the opposite direction to close the position.
    """
    pool = get_pool()
    
    # Determine exit side (opposite of entry)
    exit_side = "SELL" if order["side"] == "BUY" else "BUY"
    current_qty = abs(int(order["current_qty"]))
    
    if current_qty == 0:
        return
    
    log.info(
        f"Super order auto-exit triggered: {order['symbol']} {exit_side} {current_qty} @ {exit_price:.2f} "
        f"- {reason}"
    )
    
    # Place exit order (simple market order)
    import uuid
    exit_order_id = str(uuid.uuid4())
    
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Insert exit order
                await conn.execute(
                    """
                    INSERT INTO paper_orders
                        (order_id, user_id, instrument_token, symbol, exchange_segment,
                         side, order_type, quantity, fill_price, filled_qty, status, product_type)
                    VALUES ($1,$2,$3,$4,$5,$6,'MARKET',$7,$8,$7,'FILLED',$9)
                    """,
                    exit_order_id,
                    order["user_id"],
                    order["instrument_token"],
                    order["symbol"],
                    order["exchange_segment"],
                    exit_side,
                    current_qty,
                    exit_price,
                    order["product_type"],
                )
                
                # Close the position
                avg_price = float(order["avg_price"])
                if order["side"] == "BUY":
                    realized_pnl = (exit_price - avg_price) * current_qty
                else:
                    realized_pnl = (avg_price - exit_price) * current_qty
                
                await conn.execute(
                    """
                    UPDATE paper_positions
                    SET quantity = 0,
                        status = 'CLOSED',
                        realized_pnl = $1,
                        closed_at = NOW()
                    WHERE position_id = $2
                    """,
                    realized_pnl,
                    order["position_id"],
                )
                
                # Log execution event
                await conn.execute(
                    """
                    INSERT INTO execution_log
                        (order_id, user_id, instrument_token, event_type, status,
                         decision_price, fill_price, reason, note)
                    VALUES ($1,$2,$3,'SUPER_ORDER_EXIT','COMPLETE',$4,$5,'SUPER_ORDER',$6)
                    """,
                    exit_order_id,
                    order["user_id"],
                    order["instrument_token"],
                    exit_price,
                    exit_price,
                    reason,
                )
        
        log.info(
            f"Super order exit executed: {order['symbol']} - "
            f"Realized P&L: ₹{realized_pnl:.2f}"
        )
        
    except Exception as exc:
        log.error(f"Failed to execute super order exit: {exc}", exc_info=True)


async def _update_stop_loss(order_id: str, new_sl: float) -> None:
    """Update the stop loss price for a trailing stop order."""
    pool = get_pool()
    await pool.execute(
        "UPDATE paper_orders SET stop_loss_price = $1 WHERE order_id = $2",
        new_sl,
        order_id,
    )


# ── Module-level API ────────────────────────────────────────────────────────

def is_running() -> bool:
    """Check if the monitor is currently running."""
    return bool(_running and _monitor_task is not None and not _monitor_task.done())


async def run_once() -> None:
    """Run a single monitoring pass (admin REFRESH)."""
    await _check_all_super_orders()

