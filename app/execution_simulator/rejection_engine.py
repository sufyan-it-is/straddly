"""
app/execution_simulator/rejection_engine.py
=============================================
Validates an order before it enters the execution pipeline.
Synchronous (no I/O) — runs before the latency delay.
"""
from decimal import Decimal
from enum import Enum

from app.market_hours import is_market_open
from .execution_config import get_config


class RejectionCode(str, Enum):
    MARKET_CLOSED        = "MARKET_CLOSED"
    PRICE_OUT_OF_RANGE   = "PRICE_OUT_OF_RANGE"   # limit price > tolerance% from LTP
    QTY_TOO_LARGE        = "QTY_TOO_LARGE"
    INVALID_LIMIT_PRICE  = "INVALID_LIMIT_PRICE"  # limit price <= 0
    NO_LIQUIDITY         = "NO_LIQUIDITY"          # bid/ask depth has zero qty
    INSTRUMENT_SUSPENDED = "INSTRUMENT_SUSPENDED"
    LOT_SIZE_MISMATCH    = "LOT_SIZE_MISMATCH"     # quantity not a multiple of lot size


def check_rejection(
    order, market_snap: dict, lot_size: int = 1
) -> RejectionCode | None:
    """
    Returns a RejectionCode if the order must be rejected, else None.

    order must have attrs: exchange_segment, symbol, order_type, side,
                           quantity, price (for LIMIT), trigger (for SL)
    market_snap must have keys: ltp, ask_depth, bid_depth
    lot_size: instrument lot size — quantity must be a positive multiple of this.
    """
    cfg = get_config(order.exchange_segment)

    # 1. Market open check
    if not is_market_open(order.exchange_segment, order.symbol):
        return RejectionCode.MARKET_CLOSED

    # 2. Quantity must be a positive multiple of lot size
    if lot_size > 1 and (order.quantity <= 0 or order.quantity % lot_size != 0):
        return RejectionCode.LOT_SIZE_MISMATCH

    # 3. Quantity sanity (absolute ceiling)
    if order.quantity > cfg.max_order_qty:
        return RejectionCode.QTY_TOO_LARGE

    ltp = market_snap.get("ltp") or 0

    # 3. Limit price validation
    if order.order_type == "LIMIT":
        price = getattr(order, "limit_price", None) or 0
        if price <= 0:
            return RejectionCode.INVALID_LIMIT_PRICE
        if ltp > 0:
            deviation_pct = abs(price - ltp) / ltp * 100
            if deviation_pct > cfg.price_tolerance_pct:
                return RejectionCode.PRICE_OUT_OF_RANGE

    # 4. Liquidity check — at least some qty must exist on the required side
    depth_key = "ask_depth" if order.side == "BUY" else "bid_depth"
    depth     = market_snap.get(depth_key) or []
    total_qty = sum(level.get("qty", 0) for level in depth)
    if total_qty == 0:
        return RejectionCode.NO_LIQUIDITY

    return None  # order passes all checks
