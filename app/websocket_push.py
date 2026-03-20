"""
app/websocket_push.py
======================
Frontend WebSocket connection registry + push layer.
One ConnectionManager per user supports multiple simultaneous browser tabs.
tick_processor calls push_ticks() after each 100ms batch.
"""
import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.serializers.market_data import serialize_tick

log = logging.getLogger(__name__)


class _ConnectionManager:
    def __init__(self):
        # user_id → set of active WebSockets
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        # token subscriptions: ws → set of instrument_tokens
        self._subs: dict[WebSocket, set[int]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, user_id: str) -> None:
        await ws.accept()
        async with self._lock:
            self._connections[user_id].add(ws)
            self._subs[ws] = set()
        log.info(f"WS connected: user={user_id}  total={self.total_connections}")

    async def disconnect(self, ws: WebSocket, user_id: str) -> None:
        async with self._lock:
            self._connections[user_id].discard(ws)
            self._subs.pop(ws, None)
        log.info(f"WS disconnected: user={user_id}")

    async def subscribe(self, ws: WebSocket, tokens: list[int]) -> None:
        async with self._lock:
            self._subs.setdefault(ws, set()).update(tokens)

    async def unsubscribe(self, ws: WebSocket, tokens: list[int]) -> None:
        async with self._lock:
            if ws in self._subs:
                self._subs[ws].difference_update(tokens)

    async def send(self, ws: WebSocket, payload: dict) -> None:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception as exc:
            log.warning(f"WS send error: {exc}")

    async def push_to_user(self, user_id: str, payload: dict) -> None:
        """Push a message to ALL connections of a specific user."""
        async with self._lock:
            sockets = list(self._connections.get(user_id, set()))
        coros = [self.send(ws, payload) for ws in sockets]
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)

    async def push_ticks(self, tick_batch: dict[int, dict]) -> None:
        """
        Push market data ticks to all subscribed sockets.
        tick_batch: {instrument_token → raw_tick_dict}
        Serializes once per token, delivers only to matching subscribers.
        """
        # Build serialized payloads (one per token)
        payloads: dict[int, dict] = {}
        for token, tick in tick_batch.items():
            try:
                payloads[token] = {
                    "type": "tick",
                    "data": serialize_tick(
                        tick,
                        segment=tick.get("exchange_segment", "NSE_FNO"),
                        symbol=tick.get("symbol", ""),
                        include_depth_qty=True,
                        depth_levels=5,
                    ),
                }
            except Exception as exc:
                log.warning(f"Serialize error token={token}: {exc}")

        # Deliver
        async with self._lock:
            ws_subs = list(self._subs.items())

        tasks: list = []
        for ws, subscribed in ws_subs:
            matched = subscribed & payloads.keys()
            for token in matched:
                tasks.append(self.send(ws, payloads[token]))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def broadcast(self, payload: dict) -> None:
        """Send to every connected socket regardless of subscription."""
        async with self._lock:
            sockets = [ws for sockets in self._connections.values() for ws in sockets]
        await asyncio.gather(
            *[self.send(ws, payload) for ws in sockets],
            return_exceptions=True,
        )

    @property
    def total_connections(self) -> int:
        return sum(len(s) for s in self._connections.values())


# Singleton
ws_push = _ConnectionManager()


async def push_ticks(batch: list[dict]) -> None:
    """Compatibility helper for tick_processor.

    tick_processor passes a list of raw tick dicts; the connection manager
    expects a mapping: {instrument_token: tick_dict}.
    """
    tick_batch: dict[int, dict] = {}
    for tick in batch or []:
        try:
            token = int(tick.get("instrument_token") or 0)
        except Exception:
            token = 0
        if token:
            tick_batch[token] = tick
    if tick_batch:
        await ws_push.push_ticks(tick_batch)
