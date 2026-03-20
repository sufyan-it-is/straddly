"""
app/execution_simulator/partial_fill_monitor.py
================================================
Monitors PENDING orders with partial fills and re-executes based on depth changes.

Strategy:
  1. Order placed → immediate partial fill with available liquidity
  2. Every 30 seconds → check top 2 quantities from relevant depth side
  3. Re-execute if top 2 changed AND new max qty ≤ previous max qty
  4. This signals liquidity consumption without needing order-level tracking

This avoids constantly hammering execution on every tick while still catching
genuine liquidity refresh opportunities.
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Tuple

from app.database import get_pool
from app.market_hours import IST

log = logging.getLogger(__name__)

# Order ID → (top_qty_1, top_qty_2) snapshot
_depth_snapshots: Dict[int, Tuple[int, int]] = {}

# Check interval
_CHECK_INTERVAL_SEC = 30


class _PartialFillMonitor:
    """Periodic monitor for partial-fill retry logic based on depth changes."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="partial-fill-monitor")
        log.info("Partial fill monitor started (30s interval)")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Partial fill monitor stopped")

    async def _run_loop(self) -> None:
        """Main loop: check pending orders every 30 seconds."""
        while self._running:
            try:
                await asyncio.sleep(_CHECK_INTERVAL_SEC)
                await self._check_pending_orders()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(f"Partial fill monitor error: {exc}", exc_info=True)

    async def _check_pending_orders(self) -> None:
        """
        Check all PENDING paper orders for partial fill retry opportunities.
        
        For each pending order:
          1. Get current market depth (top 5 levels)
          2. Extract top 2 quantities by size (bid for SELL, ask for BUY)
          3. Compare with stored snapshot
          4. If changed AND new_max <= old_max → re-execute
        """
        pool = get_pool()
        
        # Fetch all PENDING orders
        rows = await pool.fetch(
            """
            SELECT 
                po.order_id,
                po.user_id,
                po.instrument_token,
                po.transaction_type,
                po.quantity,
                po.filled_quantity,
                po.order_type,
                po.limit_price
            FROM paper_orders po
            WHERE po.status = 'PENDING'
              AND po.filled_quantity < po.quantity
            ORDER BY po.created_at
            """
        )
        
        if not rows:
            return
        
        log.debug(f"Checking {len(rows)} pending orders for partial fill retry")
        
        for row in rows:
            try:
                await self._check_single_order(row)
            except Exception as exc:
                log.error(
                    f"Error checking order {row['id']}: {exc}",
                    exc_info=True
                )

    async def _check_single_order(self, order: dict) -> None:
        """Check one order and re-execute if depth conditions are met."""
        order_id = order["order_id"]
        instrument_token = order["instrument_token"]
        transaction_type = order["transaction_type"]
        order_type = order.get("order_type", "MARKET")
        limit_price = order.get("limit_price")
        
        # Get current market depth
        pool = get_pool()
        depth_row = await pool.fetchrow(
            """
            SELECT bid_depth, ask_depth
            FROM market_data
            WHERE instrument_token = $1
            """,
            instrument_token,
        )
        
        if not depth_row:
            return
        
        # Determine which side to check (BUY → ask, SELL → bid)
        if transaction_type == "BUY":
            depth_side = depth_row["ask_depth"] or []
        else:
            depth_side = depth_row["bid_depth"] or []
        
        if not depth_side:
            return
        
        # Filter depth by limit price for LIMIT orders
        # MARKET orders: use all available depth
        # LIMIT orders: only monitor depth at valid prices
        if order_type in ("LIMIT", "SLL", "SLM") and limit_price is not None:
            eligible_depth = []
            for level in depth_side:
                price = level.get("price")
                if price is None:
                    continue
                
                # BUY LIMIT: only levels at or below limit price
                if transaction_type == "BUY" and price <= limit_price:
                    eligible_depth.append(level)
                # SELL LIMIT: only levels at or above limit price
                elif transaction_type == "SELL" and price >= limit_price:
                    eligible_depth.append(level)
            
            depth_to_monitor = eligible_depth
        else:
            # MARKET orders: monitor all depth
            depth_to_monitor = depth_side
        
        if not depth_to_monitor:
            return
        
        # Get top 2 quantities by size from eligible depth
        sorted_by_qty = sorted(depth_to_monitor, key=lambda x: x.get("qty", 0), reverse=True)
        top_2_qtys = tuple(
            level.get("qty", 0)
            for level in sorted_by_qty[:2]
        )
        
        if len(top_2_qtys) < 2:
            # Not enough depth levels
            return
        
        current_top_1, current_top_2 = top_2_qtys
        
        # Get stored snapshot
        old_snapshot = _depth_snapshots.get(order_id)
        
        if old_snapshot is None:
            # First check for this order - just store snapshot
            _depth_snapshots[order_id] = top_2_qtys
            return
        
        old_top_1, old_top_2 = old_snapshot
        
        # Check trigger condition:
        # 1. Top 2 quantities have changed
        # 2. New max <= old max (signals consumption, not addition)
        if top_2_qtys != old_snapshot and current_top_1 <= old_top_1:
            depth_info = ""
            if order_type in ("LIMIT", "SLL", "SLM") and limit_price:
                depth_info = f" (filtered by limit={limit_price})"
            
            log.info(
                f"Order {order_id} ({order_type}): Depth trigger met{depth_info}. "
                f"Old top 2: ({old_top_1}, {old_top_2}), "
                f"New top 2: ({current_top_1}, {current_top_2}). "
                f"Re-executing partial fill..."
            )
            
            # Update snapshot BEFORE execution to avoid re-triggering on same data
            _depth_snapshots[order_id] = top_2_qtys
            
            # Re-execute fill attempt
            await self._execute_partial_fill(order)
        else:
            # No change or new liquidity added (not consumption) - just update snapshot
            _depth_snapshots[order_id] = top_2_qtys

    async def _execute_partial_fill(self, order: dict) -> None:
        """
        Re-attempt partial fill for a pending order.
        Reuses the fill logic from execution_engine._persist_fills.
        """
        from app.execution_simulator.fill_engine import execute_market_fill, FillEvent
        from app.database import get_pool
        from decimal import Decimal
        
        order_id = order["order_id"]  # UUID string
        user_id = order["user_id"]
        order_uuid = order_id  # Already a string UUID
        instrument_token = order["instrument_token"]
        remaining_qty = order["quantity"] - order["filled_quantity"]
        
        if remaining_qty <= 0:
            return
        
        try:
            pool = get_pool()
            
            # Get current market depth snapshot
            md_row = await pool.fetchrow(
                """
                SELECT ltp, bid_depth, ask_depth, ltt
                FROM market_data
                WHERE instrument_token = $1
                """,
                instrument_token,
            )
            
            if not md_row:
                log.debug(f"Order {order_id}: No market data available")
                return
            
            # Get instrument metadata
            im_row = await pool.fetchrow(
                """
                SELECT lot_size, tick_size, exchange_segment
                FROM instrument_master
                WHERE instrument_token = $1
                """,
                instrument_token,
            )
            
            if not im_row:
                log.error(f"Order {order_id}: Instrument not found in master")
                return
            
            lot_size = int(im_row["lot_size"] or 1)
            tick_size = Decimal(str(im_row["tick_size"] or "0.05"))
            
            # Build mock order object for fill_engine using SimpleNamespace
            from types import SimpleNamespace
            
            _o = SimpleNamespace(
                side=order["transaction_type"],
                quantity=remaining_qty,
                remaining_qty=remaining_qty,
                exchange_segment=order.get("exchange_segment", "NSE_FNO"),
                limit_price=order.get("limit_price"),
                order_type=order.get("order_type", "LIMIT"),
                symbol=order.get("symbol"),
                instrument_token=instrument_token,
                order_id=str(user_id) + str(order_id),  # Dummy for logging
            )
            
            market_snap = {
                "ltp": md_row["ltp"],
                "bid_depth": md_row["bid_depth"] or [],
                "ask_depth": md_row["ask_depth"] or [],
                "ltt": md_row["ltt"],
                "tick_size": float(tick_size),
            }
            
            # Execute fill attempt
            fills = execute_market_fill(_o, market_snap, tick_size, lot_size)
            valid_fills = [f for f in fills if getattr(f, "fill_qty", 0) > 0]
            filled_qty = int(sum(f.fill_qty for f in valid_fills))
            
            if filled_qty == 0:
                log.debug(f"Order {order_id}: No liquidity available for partial fill")
                return
            
            # Calculate average fill price
            weighted = sum(
                Decimal(str(f.fill_price)) * Decimal(f.fill_qty)
                for f in valid_fills
            )
            avg_price = weighted / Decimal(filled_qty)
            
            # Update database (merge with existing fills)
            async with pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT filled_qty, avg_fill_price FROM paper_orders WHERE id=$1",
                    order_id,
                )
                
                prev_filled = int(existing["filled_qty"] or 0) if existing else 0
                prev_avg = Decimal(str(existing["avg_fill_price"] or 0)) if existing else Decimal("0")
                
                new_filled_total = prev_filled + filled_qty
                new_remaining = order["quantity"] - new_filled_total
                
                # Weighted average of previous and new fills
                if prev_filled > 0:
                    new_avg = (
                        (prev_avg * prev_filled + avg_price * filled_qty) / new_filled_total
                    )
                else:
                    new_avg = avg_price
                
                new_status = "FILLED" if new_remaining == 0 else "PARTIAL"
                
                await conn.execute(
                    """
                    UPDATE paper_orders
                    SET filled_qty = $2,
                        avg_fill_price = $3,
                        fill_price = $3,
                        remaining_qty = $4,
                        status = $5,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    order_id, new_filled_total, new_avg, new_remaining, new_status,
                )
                
                # Insert trade records
                for fill in valid_fills:
                    await conn.execute(
                        """
                        INSERT INTO paper_trades
                            (order_id, user_id, instrument_token, exchange_segment, symbol,
                             side, fill_qty, fill_price, slippage)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                        """,
                        order_id, user_id, instrument_token, 
                        _o.exchange_segment, _o.symbol,
                        _o.side, fill.fill_qty, fill.fill_price, 
                        getattr(fill, "slippage", Decimal("0")),
                    )
                
                # Update position (reuse existing position update logic)
                from app.execution_simulator.execution_engine import _update_position
                await _update_position(conn, user_id, _o, avg_price, filled_qty)
            
            log.info(
                f"Order {order_id}: Partial fill executed. "
                f"Filled {filled_qty} @ {avg_price:.2f}. "
                f"Total filled: {new_filled_total}/{order['quantity']}. "
                f"Status: {new_status}"
            )
            
            # Clear snapshot if fully filled
            if new_remaining == 0:
                self.clear_snapshot(order_id)
            
        except Exception as exc:
            log.error(
                f"Order {order_id}: Partial fill re-execution failed: {exc}",
                exc_info=True
            )

    def clear_snapshot(self, order_id: str) -> None:
        """Clear depth snapshot when order is fully filled or cancelled."""
        _depth_snapshots.pop(order_id, None)


# Singleton
partial_fill_monitor = _PartialFillMonitor()
