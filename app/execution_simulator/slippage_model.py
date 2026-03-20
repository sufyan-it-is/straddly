"""
app/execution_simulator/slippage_model.py
==========================================
Calculates realistic price slippage based on order size vs available liquidity.
Higher order size relative to top-of-book quantity → more slippage.
MCX configured with higher aggression than NSE.
"""
from decimal import Decimal
from .execution_config import get_config, get_tick_size


def calculate_slippage(
    exchange_segment: str,
    order_qty: int,
    top_of_book_qty: int,
    tick_size: Decimal | None = None,
) -> Decimal:
    """
    slippage = base_ticks * aggression * (order_qty / top_of_book_qty)
    Clamped to [0, 10] ticks.

    Args:
        exchange_segment: e.g. 'NSE_FNO', 'MCX_FO'
        order_qty:        number of units in the order
        top_of_book_qty:  available qty at best bid/ask level
        tick_size:        instrument tick size (falls back to segment default)
    """
    cfg  = get_config(exchange_segment)
    tick = tick_size if tick_size is not None else get_tick_size(exchange_segment)

    liquidity_ratio = order_qty / max(top_of_book_qty, 1)
    raw_ticks = cfg.base_slippage_ticks * cfg.slippage_aggression * liquidity_ratio
    clamped   = max(0.0, min(raw_ticks, 10.0))
    # Round to nearest integer number of ticks
    return Decimal(str(round(clamped))) * tick
