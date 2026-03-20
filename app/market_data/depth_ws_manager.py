"""
app/market_data/depth_ws_manager.py
=====================================
Full Market Depth WebSocket — 20-Level bid/ask for the 3 index underlyings.
Connects to wss://depth-api-feed.dhan.co/twentydepth
Max 50 instruments per connection — 3 indexes fits easily in one connection.

This is a SEPARATE WS endpoint from the Live Market Feed.
Does NOT count against the 5-connection Live Feed quota.
"""
import asyncio
import json
import logging
import struct

import websockets
from websockets.exceptions import ConnectionClosed

from app.credentials.credential_store import get_depth_ws_url
from app.market_hours import is_nse_bse_ws_window_open_strict

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 10
_BACKOFF_BASE = 5
_BACKOFF_MAX  = 120
_COOLDOWN_SEC = 3600

# Request codes for depth WS
_RC_SUBSCRIBE_20  = 23
_RC_DISCONNECT    = 12

# Response codes
_FC_BID_DATA = 41
_FC_ASK_DATA = 51
_FC_DISC     = 50

# Token → pending depth (bid and ask built separately, merged on both received)
_depth_buffer: dict[int, dict] = {}


class _DepthWSManager:
    """Single connection for Full 20-Level Market Depth."""

    def __init__(self):
        self._ws         = None
        self._connected  = False
        self._attempts   = 0
        self._task: asyncio.Task | None = None
        self._tokens: list[int] = []

    async def start(self, tokens: list[int]) -> None:
        self._tokens = tokens
        if not is_nse_bse_ws_window_open_strict():
            await self.stop()
            log.info("Depth WS: skipped startup (NSE/BSE market window is closed).")
            return
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_forever(), name="depth-ws-20")

    async def reconnect(self) -> None:
        if self._task:
            self._task.cancel()
        self._ws       = None
        self._attempts = 0
        self._task = asyncio.create_task(self._run_forever(), name="depth-ws-20")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._ws:
            try:
                await self._ws.send(json.dumps({"RequestCode": _RC_DISCONNECT}))
                await self._ws.close()
            except Exception:
                pass

    async def _run_forever(self) -> None:
        while True:
            if not is_nse_bse_ws_window_open_strict():
                await asyncio.sleep(30.0)
                continue

            if self._attempts >= _MAX_ATTEMPTS:
                log.error(f"Depth WS: {_MAX_ATTEMPTS} failed attempts. Cooling down.")
                await asyncio.sleep(_COOLDOWN_SEC)
                self._attempts = 0

            try:
                url = get_depth_ws_url()
                log.info("Depth WS: Connecting…")
                async with websockets.connect(
                    url,
                    ping_interval=10,
                    ping_timeout=40,
                ) as ws:
                    self._ws        = ws
                    self._connected = True
                    self._attempts  = 0
                    log.info(f"Depth WS: Connected. Subscribing {len(self._tokens)} tokens.")

                    # Log any immediate server message (often entitlement/auth errors).
                    try:
                        await self._log_initial_server_message()
                    except Exception as exc:
                        log.error(f"Depth WS: Server closed before subscribe — {exc}")
                        raise
                    await self._subscribe(self._tokens)

                    async for message in ws:
                        if isinstance(message, bytes):
                            await self._handle_binary(message)
                        elif isinstance(message, str) and message.strip():
                            log.warning(f"Depth WS: Server text message: {message}")

            except ConnectionClosed as exc:
                log.warning(f"Depth WS: Connection closed — {exc}")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.error(f"Depth WS: Error — {exc}")
            finally:
                self._connected = False
                self._ws = None

            self._attempts += 1
            backoff = min(_BACKOFF_BASE * (2 ** (self._attempts - 1)), _BACKOFF_MAX)
            log.info(f"Depth WS: Reconnecting in {backoff}s…")
            await asyncio.sleep(backoff)

    async def _subscribe(self, tokens: list[int]) -> None:
        if not tokens or not self._ws:
            return
        # Fetch segments
        from app.database import get_pool
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT instrument_token, exchange_segment FROM instrument_master "
            "WHERE instrument_token = ANY($1::bigint[])",
            tokens,
        )
        seg_map = {r["instrument_token"]: r["exchange_segment"] for r in rows}

        msg = {
            "RequestCode":     _RC_SUBSCRIBE_20,
            "InstrumentCount": len(tokens),
            "InstrumentList": [
                {
                    "ExchangeSegment": seg_map.get(t, "NSE_FNO"),
                    "SecurityId":      str(t),
                }
                for t in tokens
            ],
        }
        await self._ws.send(json.dumps(msg))

    async def _handle_binary(self, data: bytes) -> None:
        """
        20-Level Depth response header (12 bytes):
          [0-1]  int16: message length
          [2]    byte:  response code (41=Bid, 51=Ask, 50=Disconnect)
          [3]    byte:  exchange segment
          [4-7]  int32: security_id
          [8-11] uint32: message sequence (ignore)
        Followed by 20 × 16-byte packets: float64 price, uint32 qty, uint32 orders
        """
        if len(data) < 12:
            return

        response_code = data[2]
        security_id   = struct.unpack_from("<i", data, 4)[0]

        if response_code == _FC_DISC:
            log.warning("Depth WS: Server sent disconnect packet.")
            return

        if response_code not in (_FC_BID_DATA, _FC_ASK_DATA):
            return

        levels = []
        for i in range(20):
            off = 12 + i * 16
            if off + 16 > len(data):
                break
            price  = struct.unpack_from("<d", data, off)[0]      # float64
            qty    = struct.unpack_from("<I", data, off + 8)[0]  # uint32
            levels.append({"price": round(price, 2), "qty": int(qty)})

        buf = _depth_buffer.setdefault(security_id, {})
        if response_code == _FC_BID_DATA:
            buf["bid_depth"] = levels
        else:
            buf["ask_depth"] = levels

        # Merge only when both bid and ask have been received
        if "bid_depth" in buf and "ask_depth" in buf:
            from app.market_data.tick_processor import tick_processor
            await tick_processor.push({
                "instrument_token": security_id,
                "bid_depth":        buf.pop("bid_depth"),
                "ask_depth":        buf.pop("ask_depth"),
            })

    async def _log_initial_server_message(self) -> None:
        if not self._ws:
            return
        try:
            message = await asyncio.wait_for(self._ws.recv(), timeout=0.25)
        except asyncio.TimeoutError:
            return
        if isinstance(message, str) and message.strip():
            log.warning(f"Depth WS: Initial server text message: {message}")
        # If it's bytes, it's likely a depth packet; leave it to the normal loop.

    def is_connected(self) -> bool:
        return self._connected

    def get_status(self) -> dict:
        """Status dict for Admin Dashboard."""
        return {
            "connected": self._connected,
            "tokens":    len(self._tokens),
            "attempts":  self._attempts,
        }


# Singleton
depth_ws_manager = _DepthWSManager()
