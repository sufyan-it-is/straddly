"""
app/execution_simulator/order_queue_manager.py
================================================
In-memory FIFO limit order book per (instrument_token, side, price_level).
Limit orders queue here until the market trades through their price.
on_tick() is polled on every incoming tick from tick_processor.
"""
import asyncio
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from decimal import Decimal

log = logging.getLogger(__name__)


@dataclass
class QueuedOrder:
    order_id:         str
    user_id:          str
    instrument_token: int
    side:             str          # BUY | SELL
    order_type:       str          # LIMIT | SL
    exchange_segment: str
    symbol:           str
    limit_price:      Decimal      # filled when market crosses this
    trigger_price:    Decimal      # for SL — activates limit at this price
    quantity:         int
    remaining_qty:    int = field(init=False)
    tick_size:        Decimal = Decimal("0.05")
    lot_size:         int     = 1   # fills are always whole-lot multiples

    def __post_init__(self):
        self.remaining_qty = self.quantity


# (instrument_token, side) → {price_level: deque[QueuedOrder]}
_book: dict[tuple[int, str], dict[Decimal, deque[QueuedOrder]]] = defaultdict(
    lambda: defaultdict(deque)
)

_lock = asyncio.Lock()


async def enqueue(order: QueuedOrder) -> None:
    async with _lock:
        key = (order.instrument_token, order.side)
        _book[key][order.limit_price].append(order)
    log.debug(
        f"Order {order.order_id} queued at {order.side} "
        f"{order.limit_price} for token {order.instrument_token}"
    )


async def cancel(instrument_token: int, side: str, price: Decimal, order_id: str) -> bool:
    async with _lock:
        key   = (instrument_token, side)
        queue = _book[key].get(price, deque())
        new_q = deque(o for o in queue if o.order_id != order_id)
        removed = len(queue) - len(new_q)
        _book[key][price] = new_q
    return removed > 0


async def cancel_by_id(order_id: str) -> bool:
    """Cancel an order by order_id alone, scanning all tokens/sides/price levels.
    Handles SL orders where the queued limit_price differs from the DB limit_price.
    """
    async with _lock:
        for key, levels in list(_book.items()):
            for price_level, queue in list(levels.items()):
                new_q = deque(o for o in queue if o.order_id != order_id)
                if len(new_q) < len(queue):
                    _book[key][price_level] = new_q
                    return True
    return False


async def get_fillable(
    instrument_token: int,
    side: str,
    market_price: Decimal,
    best_bid: Decimal | None = None,
    best_ask: Decimal | None = None,
) -> list[QueuedOrder]:
    """
    Returns orders whose limit price is now reachable, in FIFO order.
    
    LIMIT orders:
      BUY limit fills if market_price <= limit_price  (market came down to buy price)
      SELL limit fills if market_price >= limit_price (market went up to sell price)
    
    SL (Stop Loss) orders:
      BUY SL triggers when market_price >= trigger_price (breakout), then fills at limit_price
      SELL SL triggers when market_price <= trigger_price (breakdown), then fills at limit_price
      
    Fixed: Proper SL order trigger logic implementation
    """
    fillable: list[QueuedOrder] = []
    async with _lock:
        key  = (instrument_token, side)
        book = _book.get(key, {})
        # Sort price levels: BUY = highest limit first (most urgent), SELL = lowest first
        sorted_levels = sorted(
            book.keys(), reverse=(side == "BUY")
        )
        for price_level in sorted_levels:
            for order in book[price_level]:
                is_fillable = False
                
                if order.order_type == "SL":
                    # Stop-Loss order: check if trigger price crossed
                    if side == "BUY":
                        # BUY SL: triggers when market goes UP (stop loss on short position)
                        # OR: breakout buy when market crosses trigger
                        if market_price >= order.trigger_price:
                            # Once triggered, check if limit price is reachable
                            if market_price >= order.limit_price:
                                is_fillable = True
                    else:  # SELL
                        # SELL SL: triggers when market goes DOWN (stop loss on long position)
                        if market_price <= order.trigger_price:
                            # Once triggered, check if limit price is reachable
                            if market_price <= order.limit_price:
                                is_fillable = True
                else:
                    if order.order_type == "MARKET":
                        # Remaining MARKET quantity should keep consuming fresh
                        # depth as soon as the book updates.
                        is_fillable = True
                        fillable.append(order)
                        continue
                    # Regular LIMIT order. Prefer executable top-of-book checks,
                    # fall back to LTP when depth is unavailable.
                    if side == "BUY":
                        trigger_price = best_ask if best_ask is not None else market_price
                        if trigger_price <= price_level:
                            is_fillable = True
                    elif side == "SELL":
                        trigger_price = best_bid if best_bid is not None else market_price
                        if trigger_price >= price_level:
                            is_fillable = True
                
                if is_fillable:
                    fillable.append(order)
    
    return fillable


async def remove_filled(
    instrument_token: int, side: str, price: Decimal, order_id: str
) -> None:
    async with _lock:
        key   = (instrument_token, side)
        queue = _book[key].get(price, deque())
        _book[key][price] = deque(o for o in queue if o.order_id != order_id)


def pending_count() -> int:
    return sum(
        len(queue)
        for levels in _book.values()
        for queue in levels.values()
    )
