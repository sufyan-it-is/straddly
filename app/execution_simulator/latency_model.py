"""
app/execution_simulator/latency_model.py
==========================================
Injects realistic async latency before order processing.
"""
import asyncio
import random
import logging
from .execution_config import get_config

log = logging.getLogger(__name__)


async def apply_latency(exchange_segment: str) -> int:
    """
    Sleeps for a random duration within the configured range.
    Returns actual latency applied in milliseconds.
    """
    cfg        = get_config(exchange_segment)
    latency_ms = random.randint(cfg.latency_min_ms, cfg.latency_max_ms)
    await asyncio.sleep(latency_ms / 1000.0)
    log.debug(f"[Latency:{exchange_segment}] Applied {latency_ms}ms.")
    return latency_ms
