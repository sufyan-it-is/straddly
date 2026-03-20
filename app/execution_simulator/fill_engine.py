"""
app/execution_simulator/fill_engine.py
========================================
Walks the bid/ask ladder consuming available liquidity level by level.
Produces one FillEvent per price level hit — natural partial fills.
BUY  → consumes ask ladder from best ask upward
SELL → consumes bid ladder from best bid downward
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .slippage_model import calculate_slippage


@dataclass
class FillEvent:
    fill_price:    Decimal
    fill_qty:      int
    remaining_qty: int
    slippage:      Decimal
    timestamp:     Optional[object]   # datetime from exchange ltt


def execute_market_fill(
    order,                  # duck typing: .side, .quantity, .exchange_segment, .limit_price
    market_snap: dict,
    tick_size: Decimal,
    lot_size: int = 1,
) -> list[FillEvent]:
    """
    Walk the order book ladder. Returns a list of FillEvents.
    The list may contain one event (full fill) or many (partial fills).
    Every fill quantity is a whole-lot multiple — any leftover below one lot
    at a given depth level is deferred to the next level.
    If fills[-1].remaining_qty > 0, the order was not fully satisfied
    by the available depth.
    
    CRITICAL FIX: Respects limit price for LIMIT orders:
      BUY LIMIT: Only fills at prices <= limit_price (never worse)
      SELL LIMIT: Only fills at prices >= limit_price (never worse)
      MARKET: Fills at market depth (no limit restriction)
    """
    depth_key = "ask_depth" if order.side == "BUY" else "bid_depth"
    depth     = market_snap.get(depth_key) or []
    remaining = getattr(order, "remaining_qty", order.quantity)
    fills: list[FillEvent] = []
    _lot = max(lot_size, 1)   # guard against 0

    # Get limit price for validation (LIMIT and SL orders have limit_price)
    limit_price = getattr(order, "limit_price", None)
    if limit_price:
        limit_price = Decimal(str(limit_price))

    for level in depth:
        if remaining <= 0:
            break
        available   = level.get("qty", 0)
        if available <= 0:
            continue
        
        fill_px = Decimal(str(level["price"]))
        
        # ── CRITICAL VALIDATION: Check if fill would violate limit price ──
        if limit_price:
            if order.side == "BUY" and fill_px > limit_price:
                # BUY order: depth price is worse (higher) than limit — STOP HERE
                break
            elif order.side == "SELL" and fill_px < limit_price:
                # SELL order: depth price is worse (lower) than limit — STOP HERE
                break
        
        # Raw fill capped by both remaining and available depth
        raw_fill    = min(remaining, available)
        # Floor to the nearest whole-lot boundary
        filled_here = (raw_fill // _lot) * _lot
        if filled_here <= 0:
            # Can't fill even one lot at this level — skip
            continue

        # LIMIT / SL orders must execute at limit-or-better.
        # Applying adverse slippage here can incorrectly block otherwise valid
        # partial fills, so keep slippage only for unconstrained market fills.
        if limit_price:
            slippage = Decimal("0")
        else:
            slippage = calculate_slippage(
                order.exchange_segment, filled_here, available, tick_size
            )
            # BUY: higher price (adverse), SELL: lower price (adverse)
            fill_px = fill_px + slippage if order.side == "BUY" else fill_px - slippage

        fills.append(FillEvent(
            fill_price    = fill_px.quantize(tick_size),
            fill_qty      = filled_here,
            remaining_qty = remaining - filled_here,
            slippage      = slippage,
            timestamp     = market_snap.get("ltt"),
        ))
        remaining -= filled_here

    if not fills:
        # No depth at all — return a zero-qty event so caller knows
        fills.append(FillEvent(
            fill_price=Decimal("0"),
            fill_qty=0,
            remaining_qty=remaining,
            slippage=Decimal("0"),
            timestamp=market_snap.get("ltt"),
        ))

    return fills
