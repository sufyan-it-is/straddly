"""
MIS Auto Square-Off Scheduler

Automatically exits all open MIS (Intraday) positions before equity market close.

Active window (IST):
- NSE/BSE: 3:20 PM to 3:30 PM (continuous pulse)

Only processes positions that:
1. Status = 'OPEN'
2. product_type = 'MIS'
3. quantity != 0
4. exchange_segment belongs to NSE/BSE

Places market orders on the opposite side to close positions.
"""
import asyncio
import logging
from datetime import datetime, time
from typing import Optional
import pytz
from decimal import Decimal

from app.database import get_pool
from app.websocket_push import ws_push
from app.runtime.notifications import add_notification

logger = logging.getLogger(__name__)

# IST timezone
IST = pytz.timezone('Asia/Kolkata')

# NSE/BSE square-off active window (10 minutes before 3:30 PM close)
NSE_BSE_SQUAREOFF_START = time(hour=15, minute=20)
NSE_BSE_SQUAREOFF_END = time(hour=15, minute=30)

# Pulse intervals
DEFAULT_PULSE_SECONDS = 60
ACTIVE_WINDOW_PULSE_SECONDS = 1


class MISAutoSquareoffScheduler:
    """
    Scheduler for automatic MIS position square-off before market close.
    """
    
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_run_nse = None
        self.last_run_at: Optional[datetime] = None
        self.last_run_result: Optional[dict] = None
        self.last_run_error: Optional[str] = None
        self._stats = {
            'total_squared_off': 0,
            'total_errors': 0,
            'last_run_at': None,
            'positions_closed': 0,
        }
    
    async def start(self):
        """Start the scheduler."""
        if self._running:
            logger.warning("MIS auto-square-off scheduler already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("🔄 MIS auto-square-off scheduler started (NSE/BSE: 3:20 PM-3:30 PM, continuous pulse)")
    
    async def stop(self):
        """Stop the scheduler."""
        if not self._running:
            return
        
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("⏹️ MIS auto-square-off scheduler stopped")
    
    async def _run_loop(self):
        """Main scheduler loop."""
        while self._running:
            try:
                await self._check_and_run()
                now_ist = datetime.now(IST).time()
                in_active_window = NSE_BSE_SQUAREOFF_START <= now_ist < NSE_BSE_SQUAREOFF_END
                pulse_seconds = ACTIVE_WINDOW_PULSE_SECONDS if in_active_window else DEFAULT_PULSE_SECONDS
                await asyncio.sleep(pulse_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in MIS auto-square-off scheduler loop: {e}")
                self.last_run_error = str(e)
                await asyncio.sleep(DEFAULT_PULSE_SECONDS)
    
    async def _check_and_run(self):
        """Check if it's time to run auto square-off."""
        now_ist = datetime.now(IST)
        today_date = now_ist.date()
        current_time = now_ist.time()
        
        # Run continuously in equity close window: 15:20 <= time < 15:30 IST
        if current_time >= NSE_BSE_SQUAREOFF_START and current_time < NSE_BSE_SQUAREOFF_END:
            logger.info("⏰ Running NSE/BSE MIS auto-square-off pulse (15:20-15:30 IST)")
            # Covers NSE/BSE equities + derivatives across known segment label variants.
            result = await self.run_once(
                exchanges=['NSE_EQ', 'NSE_FNO', 'NSE_FO', 'NSE', 'BSE_EQ', 'BSE_FO', 'BSE_FNO', 'BSE']
            )
            self._last_run_nse = now_ist
            self.last_run_at = now_ist
            self.last_run_result = result
            self.last_run_error = None
    
    async def run_once(self, exchanges: list[str] | None = None) -> dict:
        """
        Execute auto square-off for all open MIS positions.
        
        Args:
            exchanges: List of exchange segments to process (e.g., ['NSE_EQ', 'NSE_FNO'])
                      If None, processes all exchanges.
        
        Returns:
            dict with 'positions_closed' and 'errors' counts
        """
        start_time = datetime.now(IST)
        pool = get_pool()
        
        positions_closed = 0
        errors = 0
        error_details = []
        
        try:
            # Find all open MIS positions
            query = """
                SELECT 
                    position_id, user_id, instrument_token, symbol, 
                    exchange_segment, quantity, avg_price, product_type
                FROM paper_positions
                WHERE status = 'OPEN'
                  AND product_type = 'MIS'
                  AND quantity != 0
            """
            params = []
            
            if exchanges:
                query += " AND exchange_segment = ANY($1::text[])"
                params.append(exchanges)
            
            positions = await pool.fetch(query, *params)
            
            logger.info(f"Found {len(positions)} open MIS positions to square-off")
            
            # Place exit orders for each position
            for pos in positions:
                try:
                    position_id = pos['position_id']
                    user_id = pos['user_id']
                    instrument_token = pos['instrument_token']
                    symbol = pos['symbol']
                    exchange_segment = pos['exchange_segment']
                    quantity = int(pos['quantity'])
                    avg_price = float(pos['avg_price'])
                    
                    # Determine exit side (opposite of current position)
                    if quantity > 0:
                        exit_side = 'SELL'
                        exit_qty = quantity
                    else:
                        exit_side = 'BUY'
                        exit_qty = abs(quantity)
                    
                    # Get current LTP for the instrument from market_data table
                    ltp_row = await pool.fetchrow(
                        "SELECT ltp FROM market_data WHERE instrument_token = $1",
                        instrument_token
                    )
                    ltp = float(ltp_row['ltp']) if ltp_row and ltp_row['ltp'] else avg_price
                    
                    # Place market exit order via the orders router logic
                    import uuid
                    order_id = str(uuid.uuid4())
                    
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            # Insert exit order as FILLED
                            await conn.execute(
                                """
                                INSERT INTO paper_orders
                                    (order_id, user_id, instrument_token, symbol, exchange_segment,
                                     side, order_type, quantity, fill_price, filled_qty,
                                     status, product_type, security_id, placed_at, remarks)
                                VALUES ($1, $2, $3, $4, $5, $6, 'MARKET', $7, $8, $7, 'FILLED', 'MIS', $3, NOW(), 'Auto square-off')
                                """,
                                order_id, user_id, instrument_token, symbol, exchange_segment,
                                exit_side, exit_qty, ltp
                            )
                            
                            # Calculate realized P&L
                            if quantity > 0:  # Long position
                                realized_pnl = (ltp - avg_price) * exit_qty
                            else:  # Short position
                                realized_pnl = (avg_price - ltp) * exit_qty
                            
                            # Close the position
                            await conn.execute(
                                """
                                UPDATE paper_positions
                                SET quantity = 0,
                                    status = 'CLOSED',
                                    realized_pnl = COALESCE(realized_pnl, 0) + $1,
                                    closed_at = NOW()
                                WHERE position_id = $2
                                """,
                                realized_pnl, position_id
                            )
                    
                    positions_closed += 1
                    logger.info(
                        f"✓ Auto-squared-off MIS position: user={user_id}, symbol={symbol}, "
                        f"qty={quantity}, exit_price={ltp:.2f}, pnl={realized_pnl:.2f}"
                    )
                    
                    # Send real-time notification to user via WebSocket
                    try:
                        notification_payload = {
                            "type": "position_auto_squareoff",
                            "data": {
                                "symbol": symbol,
                                "exchange_segment": exchange_segment,
                                "quantity": quantity,
                                "exit_side": exit_side,
                                "exit_price": ltp,
                                "avg_price": avg_price,
                                "realized_pnl": realized_pnl,
                                "timestamp": datetime.now(IST).isoformat(),
                                "reason": "Intraday auto square-off at market close",
                                "order_id": order_id,
                            },
                        }
                        await ws_push.push_to_user(str(user_id), notification_payload)
                        logger.debug(f"Sent auto-squareoff notification to user {user_id}")
                    except Exception as notif_err:
                        logger.warning(f"Failed to send WebSocket notification to user {user_id}: {notif_err}")
                    
                except Exception as e:
                    errors += 1
                    error_msg = f"Failed to square-off position {pos.get('position_id')}: {str(e)}"
                    error_details.append(error_msg)
                    logger.error(error_msg)
            
            # Update stats
            self._stats['total_squared_off'] += positions_closed
            self._stats['total_errors'] += errors
            self._stats['last_run_at'] = start_time
            self._stats['positions_closed'] = positions_closed
            
            duration = (datetime.now(IST) - start_time).total_seconds()
            
            logger.info(
                f"✓ MIS auto-square-off completed: {positions_closed} positions closed, "
                f"{errors} errors, duration={duration:.1f}s"
            )
            
            # Send system notification for admin dashboard
            if positions_closed > 0:
                exchange_label = ", ".join(exchanges) if exchanges else "All exchanges"
                await add_notification(
                    category="scheduler",
                    severity="info",
                    title="MIS Auto Square-Off Completed",
                    message=f"Automatically closed {positions_closed} MIS position(s) at market close ({exchange_label}).",
                    details={
                        "positions_closed": positions_closed,
                        "errors": errors,
                        "exchanges": exchanges or "all",
                        "run_time_ist": start_time.isoformat(),
                        "duration_seconds": duration,
                    },
                    dedupe_key=f"mis_auto_squareoff:{start_time.date()}",
                    dedupe_ttl_seconds=3600,
                )
            
            # Alert admin if there were errors
            if errors > 0:
                await add_notification(
                    category="scheduler",
                    severity="warning",
                    title="MIS Auto Square-Off Errors",
                    message=f"Failed to close {errors} MIS position(s). Manual intervention may be required.",
                    details={
                        "error_count": errors,
                        "error_details": error_details[:10],  # limit to first 10 errors
                        "positions_closed": positions_closed,
                        "exchanges": exchanges or "all",
                        "run_time_ist": start_time.isoformat(),
                    },
                    dedupe_key=f"mis_auto_squareoff_errors:{start_time.date()}",
                    dedupe_ttl_seconds=3600,
                )
            
            result = {
                'positions_closed': positions_closed,
                'errors': errors,
                'error_details': error_details,
                'duration_seconds': duration,
                'run_at': start_time.isoformat()
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Critical error in MIS auto-square-off: {e}", exc_info=True)
            self._stats['total_errors'] += 1
            self.last_run_error = str(e)
            raise
    
    def get_stats(self) -> dict:
        """Get scheduler statistics."""
        return {
            **self._stats,
            'running': self._running,
            'last_run_nse': self._last_run_nse.isoformat() if self._last_run_nse else None,
        }


# Singleton instance
mis_auto_squareoff = MISAutoSquareoffScheduler()
