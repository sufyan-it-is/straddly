"""
End-of-Day Charge Calculation Scheduler

Automatically calculates brokerage and statutory charges for closed positions
at market close times:
- NSE/BSE: 4:00 PM IST (after 3:30 PM market close)
- MCX: 12:00 AM IST (after 11:30 PM-11:55 PM market close)

Only processes positions that:
1. Status = 'CLOSED'
2. charges_calculated = FALSE
3. closed_at is within the current trading day
"""
import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import List, Optional, Tuple
import pytz

from app.database import get_pool, init_db
from app.services.charge_calculator import calculate_position_charges

logger = logging.getLogger(__name__)

# IST timezone
IST = pytz.timezone('Asia/Kolkata')

# Market close times (IST)
NSE_BSE_CHARGE_TIME = time(hour=16, minute=0)  # 4:00 PM
MCX_CHARGE_TIME = time(hour=0, minute=0)       # 12:00 AM (midnight)


def normalize_enums(exchange_segment: Optional[str], product_type: Optional[str], instrument_type: Optional[str]) -> Tuple[str, str, str]:
    """
    Normalize database enum values to strict enum contract.
    
    Maps legacy/variant values from database to canonical enums:
    - exchange_segment: NSE_EQ, NSE_FNO, BSE_EQ, MCX_COMM
    - product_type: MIS, NORMAL
    - instrument_type: EQUITY, FUTIDX, FUTSTK, OPTIDX, OPTSTK, FUTCOM, OPTCOM
    """
    # Normalize exchange_segment
    seg = str(exchange_segment or '').upper().strip()
    if 'FNO' in seg or 'NIFTY' in seg or 'BANKNIFTY' in seg:
        seg = 'NSE_FNO'
    elif 'BSE' in seg and 'EQ' in seg:
        seg = 'BSE_EQ'
    elif 'BSE' in seg:
        seg = 'BSE_EQ'
    elif 'MCX' in seg or 'COMMODITY' in seg:
        seg = 'MCX_COMM'
    elif 'EQ' in seg or 'EQUITY' in seg or seg.startswith('NSE'):
        seg = 'NSE_EQ'
    else:
        seg = 'NSE_EQ'  # Default
    
    # Normalize product_type
    prod = str(product_type or '').upper().strip()
    if 'DELIVERY' in prod or 'NORMAL' in prod or prod == 'D':
        prod = 'NORMAL'
    elif 'MIS' in prod or 'INTRADAY' in prod or prod == 'I':
        prod = 'MIS'
    else:
        prod = 'MIS'  # Default
    
    # Normalize instrument_type
    inst = str(instrument_type or '').upper().strip()
    if inst == 'STOCK' or inst == 'EQUITY' or 'EQ' in inst:
        inst = 'EQUITY'
    elif inst in ('FUTIDX', 'NIFTY', 'BANKNIFTY'):
        inst = 'FUTIDX'
    elif inst in ('FUTSTK', 'STOCK_FUTURE', 'FUTSTK'):
        inst = 'FUTSTK'
    elif inst in ('OPTIDX', 'NIFTY_OPTION', 'OPTIDX'):
        inst = 'OPTIDX'
    elif inst in ('OPTSTK', 'STOCK_OPTION', 'OPTSTK'):
        inst = 'OPTSTK'
    elif inst == 'FUTCOM':
        inst = 'FUTCOM'
    elif inst == 'OPTCOM':
        inst = 'OPTCOM'
    else:
        inst = 'EQUITY'  # Default
    
    logger.debug(f"Enum normalization: {exchange_segment}/{product_type}/{instrument_type} → {seg}/{prod}/{inst}")
    return seg, prod, inst


class ChargeCalculationScheduler:
    """
    Scheduler for end-of-day charge calculation.
    """
    
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_run_nse = None
        self._last_run_mcx = None
        self._stats = {
            'total_processed': 0,
            'total_errors': 0,
            'last_run_at': None,
            'last_run_duration': None,
        }
    
    async def start(self):
        """Start the scheduler."""
        if self._running:
            logger.warning("Charge calculation scheduler already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("🔄 Charge calculation scheduler started")
    
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
        logger.info("⏹️ Charge calculation scheduler stopped")
    
    async def _run_loop(self):
        """Main scheduler loop."""
        while self._running:
            try:
                await self._check_and_run()
                # Check every 5 minutes
                await asyncio.sleep(300)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in charge calculation scheduler loop: {e}")
                await asyncio.sleep(60)
    
    async def _check_and_run(self):
        """Check if it's time to run charge calculation."""
        now_ist = datetime.now(IST)
        today_date = now_ist.date()
        current_time = now_ist.time()
        
        # Check NSE/BSE (4:00 PM IST)
        if (current_time >= NSE_BSE_CHARGE_TIME and 
            (self._last_run_nse is None or self._last_run_nse.date() < today_date)):
            logger.info("⏰ Running NSE/BSE charge calculation (4:00 PM IST)")
            await self.run_once(exchanges=['NSE', 'BSE'])
            self._last_run_nse = now_ist
        
        # Check MCX (12:00 AM IST - midnight)
        if (current_time >= MCX_CHARGE_TIME and 
            current_time < time(hour=1, minute=0) and
            (self._last_run_mcx is None or self._last_run_mcx.date() < today_date)):
            logger.info("⏰ Running MCX charge calculation (12:00 AM IST)")
            await self.run_once(exchanges=['MCX'])
            self._last_run_mcx = now_ist
    
    async def run_once(
        self,
        exchanges: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        closed_from: Optional[datetime] = None,
        closed_to: Optional[datetime] = None,
    ) -> dict:
        """
        Manually trigger charge calculation.
        
        Args:
            exchanges: List of exchanges to process ['NSE', 'BSE', 'MCX']
                      If None, processes all exchanges
        
        Returns:
            Statistics dictionary
        """
        start_time = datetime.now()
        logger.info(f"🧮 Starting charge calculation for exchanges: {exchanges or 'ALL'}")
        
        try:
            pool = get_pool()
            if pool is None:
                logger.error("❌ Database pool not initialized! Calling init_db()...")
                await init_db()
                pool = get_pool()
            
            if pool is None:
                raise RuntimeError("Database pool initialization failed - pool is still None after init_db()")
            
            # Build optional filters safely
            filters = []
            params = []

            if exchanges:
                safe_exchanges = [str(ex).strip().upper() for ex in exchanges if str(ex).strip()]
                if safe_exchanges:
                    like_clauses = []
                    for ex in safe_exchanges:
                        params.append(f"%{ex}%")
                        like_clauses.append(f"pp.exchange_segment ILIKE ${len(params)}")
                    filters.append("(" + " OR ".join(like_clauses) + ")")

            if user_id:
                params.append(str(user_id))
                filters.append(f"pp.user_id = ${len(params)}::uuid")

            if closed_from is not None:
                params.append(closed_from)
                filters.append(f"pp.closed_at >= ${len(params)}")

            if closed_to is not None:
                params.append(closed_to)
                filters.append(f"pp.closed_at <= ${len(params)}")

            extra_where = f" AND {' AND '.join(filters)}" if filters else ""
            
            # Get uncalculated closed positions
            query = f"""
                SELECT 
                    pp.position_id,
                    pp.user_id,
                    pp.quantity,
                    pp.avg_price,
                    pp.realized_pnl,
                    pp.symbol,
                    pp.exchange_segment,
                    pp.product_type,
                    pp.instrument_token,
                    pp.opened_at,
                    pp.closed_at,
                    u.brokerage_plan_equity_id,
                    u.brokerage_plan_futures_id,
                    im.instrument_type,
                    im.option_type
                FROM paper_positions pp
                JOIN users u ON u.id = pp.user_id
                LEFT JOIN instrument_master im ON im.instrument_token = pp.instrument_token
                WHERE pp.status = 'CLOSED'
                  AND pp.charges_calculated = FALSE
                  AND pp.closed_at IS NOT NULL
                {extra_where}
                ORDER BY pp.closed_at ASC
            """
            
            rows = await pool.fetch(query, *params)
            total = len(rows)
            
            if total == 0:
                logger.info("✅ No positions to process")
                return {'processed': 0, 'errors': 0, 'skipped': 0}
            
            logger.info(f"📊 Found {total} positions to process")
            
            processed = 0
            errors = 0
            skipped = 0
            
            for row in rows:
                try:
                    await self._calculate_and_update_charges(dict(row), pool)
                    processed += 1
                    
                    if processed % 10 == 0:
                        logger.info(f"Progress: {processed}/{total}")
                        
                except Exception as e:
                    logger.error(f"Error processing position {row['position_id']}: {e}")
                    errors += 1
            
            duration = (datetime.now() - start_time).total_seconds()
            
            # Update stats
            self._stats['total_processed'] += processed
            self._stats['total_errors'] += errors
            self._stats['last_run_at'] = start_time.isoformat()
            self._stats['last_run_duration'] = duration
            
            logger.info(f"✅ Charge calculation complete: {processed} processed, {errors} errors, {duration:.2f}s")
            
            return {
                'processed': processed,
                'errors': errors,
                'skipped': skipped,
                'duration_seconds': duration
            }
            
        except Exception as e:
            logger.error(f"Fatal error in charge calculation: {e}")
            raise
    
    async def _calculate_and_update_charges(self, position: dict, pool):
        """
        Calculate charges for a position and update database.
        
        IMPORTANT: Charges are ALWAYS calculated, even for zero-brokerage plans.
        Statutory charges (STT, exchange charges, SEBI, etc.) are regulatory requirements
        and must be deducted from P&L regardless of brokerage plan.
        """
        try:
            # Get brokerage plan
            is_futures = 'FUT' in (position.get('instrument_type') or '')
            plan_id = position.get('brokerage_plan_futures_id') if is_futures else position.get('brokerage_plan_equity_id')
            
            # Fetch brokerage plan details if available
            plan = None
            if plan_id:
                plan = await pool.fetchrow(
                    "SELECT flat_fee, percent_fee FROM brokerage_plans WHERE plan_id = $1",
                    plan_id
                )
            
            # If NO plan or plan not found, use ZERO brokerage (but still calculate statutory charges)
            # This ensures that traders on zero-brokerage plans still have stat charges applied to P&L
            if not plan:
                logger.info(f"No valid brokerage plan for user {position['user_id']}, using zero brokerage")
                flat_fee = 0.0
                percent_fee = 0.0
            else:
                flat_fee = float(plan['flat_fee'] or 0)
                percent_fee = float(plan['percent_fee'] or 0)
            
            # Determine exit price (use LTP or avg_price as last resort)
            ltp_row = await pool.fetchrow(
                "SELECT ltp FROM market_data WHERE instrument_token = $1",
                position['instrument_token']
            )
            exit_price = float(ltp_row['ltp']) if ltp_row and ltp_row['ltp'] else float(position['avg_price'])

            # Closed rows often have pp.quantity=0 after square-off.
            # Derive effective traded quantity and side-wise prices from filled orders.
            order_stats = await pool.fetchrow(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN po.side = 'BUY'  THEN COALESCE(po.filled_qty, 0) ELSE 0 END), 0) AS buy_qty,
                    COALESCE(SUM(CASE WHEN po.side = 'BUY'  THEN COALESCE(po.filled_qty, 0) * COALESCE(po.fill_price, 0) ELSE 0 END), 0) AS buy_value,
                    COALESCE(SUM(CASE WHEN po.side = 'SELL' THEN COALESCE(po.filled_qty, 0) ELSE 0 END), 0) AS sell_qty,
                    COALESCE(SUM(CASE WHEN po.side = 'SELL' THEN COALESCE(po.filled_qty, 0) * COALESCE(po.fill_price, 0) ELSE 0 END), 0) AS sell_value
                FROM paper_orders po
                WHERE po.user_id = $1::uuid
                  AND po.instrument_token = $2
                  AND po.status = 'FILLED'
                  AND ($3::timestamptz IS NULL OR po.placed_at >= $3)
                  AND ($4::timestamptz IS NULL OR po.placed_at <= $4)
                """,
                str(position['user_id']),
                position['instrument_token'],
                position.get('opened_at'),
                position.get('closed_at'),
            )

            buy_qty = int(order_stats['buy_qty'] or 0) if order_stats else 0
            sell_qty = int(order_stats['sell_qty'] or 0) if order_stats else 0
            buy_value = float(order_stats['buy_value'] or 0) if order_stats else 0.0
            sell_value = float(order_stats['sell_value'] or 0) if order_stats else 0.0

            effective_qty = max(abs(int(position.get('quantity') or 0)), buy_qty, sell_qty)
            if effective_qty <= 0:
                logger.warning(f"Skipping {position['position_id']}: effective quantity is zero")
                return

            effective_buy_price = (buy_value / buy_qty) if buy_qty > 0 else float(position['avg_price'])
            effective_sell_price = (sell_value / sell_qty) if sell_qty > 0 else exit_price
            
            # Calculate charges (ALWAYS, even for zero-brokerage plans)
            # Statutory charges are mandatory regulatory requirements
            is_option = position.get('option_type') in ('CE', 'PE')
            
            # Normalize enums from database to strict contract
            seg_norm, prod_norm, inst_norm = normalize_enums(
                position.get('exchange_segment'),
                position.get('product_type'),
                position.get('instrument_type')
            )
            
            logger.info(f"Processing {position['position_id']}: {seg_norm}/{prod_norm}/{inst_norm}, is_option={is_option}")
            
            charges = calculate_position_charges(
                quantity=effective_qty,
                buy_price=effective_buy_price,
                sell_price=effective_sell_price,
                exchange_segment=seg_norm,
                product_type=prod_norm,
                instrument_type=inst_norm,
                brokerage_flat=flat_fee,
                brokerage_percent=percent_fee,
                is_option=is_option
            )
            
            logger.info(f"  Charges breakdown: STT={charges['stt_ctt_charge']:.2f}, "
                       f"Stamp={charges['stamp_duty']:.2f}, Exchange={charges['exchange_charge']:.2f}, "
                       f"SEBI={charges['sebi_charge']:.2f}, GST={charges['gst_charge']:.2f}, "
                       f"Platform={charges['platform_cost']:.2f}, Trade_Expense={charges['trade_expense']:.2f}, "
                       f"Total={charges['total_charges']:.2f}")
            
            # Update position with calculated charges
            await pool.execute(
                """
                UPDATE paper_positions
                SET
                    brokerage_charge = $1,
                    stt_ctt_charge = $2,
                    exchange_charge = $3,
                    sebi_charge = $4,
                    stamp_duty = $5,
                    ipft_charge = $6,
                    gst_charge = $7,
                    platform_cost = $8,
                    trade_expense = $9,
                    total_charges = $10,
                    charges_calculated = TRUE,
                    charges_calculated_at = NOW()
                WHERE position_id = $11
                """,
                charges['brokerage_charge'],
                charges['stt_ctt_charge'],
                charges['exchange_charge'],
                charges['sebi_charge'],
                charges['stamp_duty'],
                charges['ipft_charge'],
                charges['gst_charge'],
                charges['platform_cost'],
                charges['trade_expense'],
                charges['total_charges'],
                position['position_id']
            )
            
            # Create ledger entry for realized P&L
            net_pnl = float(position.get('realized_pnl', 0)) - charges['total_charges']
            
            # Get current wallet balance
            current_balance_row = await pool.fetchrow(
                "SELECT COALESCE(MAX(balance_after), 0) as current_balance FROM ledger_entries WHERE user_id = $1",
                position['user_id']
            )
            current_balance = float(current_balance_row['current_balance']) if current_balance_row else 0.0
            new_balance = current_balance + net_pnl
            
            # Create ledger entry
            symbol = position.get('symbol') or '—'
            seg = position.get('exchange_segment') or ''
            prod = position.get('product_type') or 'MIS'
            desc = f"Realized P&L — {symbol} ({seg}, {prod})"
            
            await pool.execute(
                """
                INSERT INTO ledger_entries (user_id, created_at, description, debit, credit, balance_after)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                position['user_id'],
                position.get('closed_at') or None,
                desc,
                abs(net_pnl) if net_pnl < 0 else None,  # debit if loss
                net_pnl if net_pnl > 0 else None,       # credit if profit
                new_balance
            )
            
            logger.debug(f"✓ Calculated charges for position {position['position_id']}: ₹{charges['total_charges']:.2f}, net P&L: ₹{net_pnl:.2f}")
            
            
        except Exception as e:
            logger.error(f"ERROR calculating charges for position {position.get('position_id')}: {e}", exc_info=True)
            if isinstance(e, KeyError):
                logger.error(f"  Missing key in charges dict. Available keys: {list(charges.keys()) if 'charges' in locals() else 'charges not calculated'}")
            raise
    
    def get_stats(self) -> dict:
        """Get scheduler statistics."""
        return {
            **self._stats,
            'running': self._running,
            'last_run_nse': self._last_run_nse.isoformat() if self._last_run_nse else None,
            'last_run_mcx': self._last_run_mcx.isoformat() if self._last_run_mcx else None,
        }


# Global singleton instance
charge_calculation_scheduler = ChargeCalculationScheduler()
