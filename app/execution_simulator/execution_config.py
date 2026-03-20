"""
app/execution_simulator/execution_config.py
=============================================
All configurable execution simulation parameters.
Values loaded from system_config table at startup.
Editable from Admin Dashboard without code changes.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar


@dataclass
class ExchangeConfig:
    latency_min_ms:      int   = 50
    latency_max_ms:      int   = 300
    base_slippage_ticks: int   = 1       # multiplied by tick_size
    slippage_aggression: float = 1.0     # 1.0 = normal, 2.0 = aggressive
    max_order_qty:       int   = 10_000
    fill_timeout_sec:    int   = 60      # reject if no fill after this
    price_tolerance_pct: float = 5.0    # reject if limit > 5% from LTP


# Per-segment defaults — overridden at runtime from system_config
EXCHANGE_CONFIG: dict[str, ExchangeConfig] = {
    "NSE_EQ":  ExchangeConfig(latency_min_ms=50,  latency_max_ms=200,
                               base_slippage_ticks=1, slippage_aggression=1.0),
    "NSE_FNO": ExchangeConfig(latency_min_ms=50,  latency_max_ms=250,
                               base_slippage_ticks=1, slippage_aggression=1.0),
    "BSE_EQ":  ExchangeConfig(latency_min_ms=60,  latency_max_ms=250,
                               base_slippage_ticks=1, slippage_aggression=1.0),
    "MCX_FO":  ExchangeConfig(latency_min_ms=80,  latency_max_ms=350,
                               base_slippage_ticks=2, slippage_aggression=1.5),
    "IDX_I":   ExchangeConfig(latency_min_ms=30,  latency_max_ms=150,
                               base_slippage_ticks=1, slippage_aggression=1.0),
}

# Fallback tick sizes (canonical source = instrument_master.tick_size)
TICK_SIZES: dict[str, Decimal] = {
    "NSE_EQ":  Decimal("0.05"),
    "NSE_FNO": Decimal("0.05"),
    "BSE_EQ":  Decimal("0.05"),
    "MCX_FO":  Decimal("1.00"),
    "IDX_I":   Decimal("0.05"),
}


def get_config(segment: str) -> ExchangeConfig:
    return EXCHANGE_CONFIG.get(segment.upper(), ExchangeConfig())


def get_tick_size(segment: str) -> Decimal:
    return TICK_SIZES.get(segment.upper(), Decimal("0.05"))
