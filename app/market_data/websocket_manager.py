"""
app/market_data/websocket_manager.py
======================================
Manages 5 persistent DhanHQ Live Market Feed WebSocket connections.
Each connection handles up to 5,000 instrument tokens.
Subscriptions are sent in batches of 100 (DhanHQ per-message limit).
Exponential backoff reconnection — max 10 attempts then 1-hour cooldown.
"""
import asyncio
import json
import logging
import struct
import time
from datetime import datetime

import websockets
from websockets.exceptions import ConnectionClosed

from app.config import get_settings
from app.credentials.credential_store import get_ws_url_candidates
from app.market_hours import IST, is_nse_bse_ws_window_open_strict, record_exchange_tick_time
from app.runtime.notifications import add_notification

log = logging.getLogger(__name__)

cfg = get_settings()

# Segment exclusion hook (kept for emergency debugging).
# Default: subscribe everything.
_LIVE_FEED_EXCLUDE_SEGMENTS: set[str] = set()

# ── Global connect gate (prevents WS handshake storms) ─────────────────────
# Dhan will reject rapid repeated WS handshakes with HTTP 429.
_CONNECT_GATE_LOCK = asyncio.Lock()
_CONNECT_COOLDOWN_UNTIL: float = 0.0
_LAST_CONNECT_ATTEMPT: float = 0.0
_MIN_CONNECT_INTERVAL_SEC = 15.0
_RATE_LIMIT_COOLDOWN_SEC = 120.0


async def _await_connect_gate(*, slot: int) -> None:
    """Ensure WS handshake attempts are globally rate-limited across all slots."""
    global _CONNECT_COOLDOWN_UNTIL, _LAST_CONNECT_ATTEMPT
    while True:
        wait_for = 0.0
        async with _CONNECT_GATE_LOCK:
            now = time.monotonic()
            if now < _CONNECT_COOLDOWN_UNTIL:
                wait_for = _CONNECT_COOLDOWN_UNTIL - now
            else:
                since_last = now - _LAST_CONNECT_ATTEMPT
                if since_last < _MIN_CONNECT_INTERVAL_SEC:
                    wait_for = _MIN_CONNECT_INTERVAL_SEC - since_last
                else:
                    _LAST_CONNECT_ATTEMPT = now
                    return
        # small deterministic staggering so slots don't re-align
        await asyncio.sleep(wait_for + (slot * 0.25))


async def _trigger_global_cooldown() -> None:
    global _CONNECT_COOLDOWN_UNTIL
    now = time.monotonic()
    _CONNECT_COOLDOWN_UNTIL = max(_CONNECT_COOLDOWN_UNTIL, now + _RATE_LIMIT_COOLDOWN_SEC)

# ── Reconnection parameters ─────────────────────────────────────────────────
_MAX_ATTEMPTS  = 10
_BACKOFF_BASE  = 5      # seconds
_BACKOFF_MAX   = 120
_COOLDOWN_SEC  = 3600   # 1 hour after max attempts exhausted

# ── Request codes (DhanHQ v2) ────────────────────────────────────────────────
# Subscribe codes: Ticker=15, Quote=17, Full=21  (v2 only allows 15/17/21)
# Unsubscribe code = subscribe code + 1
_RC_SUBSCRIBE_FULL   = 21   # Full packet: LTP + OHLC + 5-level depth + OI
_RC_SUBSCRIBE_QUOTE  = 17   # Quote: LTP + OHLC, no depth
_RC_SUBSCRIBE_TICKER = 15   # Ticker: LTP + LTT only
_RC_UNSUBSCRIBE      = 22   # Full unsubscribe (21 + 1)
_RC_DISCONNECT       = 12

# ── Feed response codes ──────────────────────────────────────────────────────
_FC_TICKER   = 2
_FC_QUOTE    = 4
_FC_FULL     = 8
_FC_PREV_CLOSE = 6
_FC_OI       = 5
_FC_DISCONNECT = 50


class _SingleWSConnection:
    """One WebSocket connection — handles one of the 5 slots."""

    def __init__(self, slot: int):
        self.slot        = slot
        self._ws         = None
        self._attempts   = 0
        self._connected  = False
        self._first_tick = True
        self._task: asyncio.Task | None = None
        # Keep FULL (21) subscriptions to preserve depth for execution logic.
        # We do not auto-downgrade to quote/ticker because those packets omit depth.
        self._subscribe_rc = _RC_SUBSCRIBE_FULL
        self._ws_url_idx = 0

    def _downgrade_subscribe_rc(self) -> bool:
        """Depth-required mode: never downgrade below FULL feed."""
        return False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_forever(), name=f"ws-{self.slot}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._ws:
            try:
                await self._ws.send(json.dumps({"RequestCode": _RC_DISCONNECT}))
                await self._ws.close()
            except Exception:
                pass
        self._attempts  = 0   # reset so next start() begins from attempt 0
        self._connected = False
        self._ws        = None

    async def reconnect(self) -> None:
        await self.stop()
        self._attempts = 0
        await self.start()

    async def _run_forever(self) -> None:
        while True:
            if not is_nse_bse_ws_window_open_strict():
                # Hard gate: do not even attempt handshakes outside NSE/BSE window.
                await asyncio.sleep(30.0)
                continue

            rate_limited = False
            if self._attempts >= _MAX_ATTEMPTS:
                log.error(
                    f"WS-{self.slot}: {_MAX_ATTEMPTS} failed attempts. "
                    f"Cooling down for {_COOLDOWN_SEC}s."
                )
                await asyncio.sleep(_COOLDOWN_SEC)
                self._attempts = 0

            try:
                await _await_connect_gate(slot=self.slot)
                urls = get_ws_url_candidates()
                url = urls[self._ws_url_idx % len(urls)]
                log.info(f"WS-{self.slot}: Connecting…")
                async with websockets.connect(
                    url,
                    ping_interval=10,
                    ping_timeout=40,
                    close_timeout=5,
                ) as ws:
                    self._ws       = ws
                    self._connected = True
                    self._attempts  = 0
                    log.info(f"WS-{self.slot}: Connected.")

                    # Some servers send an immediate text error/welcome message.
                    # We normally only parse binary feed packets; log the first
                    # message (if any) so entitlement/auth issues are visible.
                    try:
                        await self._log_initial_server_message()
                    except Exception as exc:
                        # If the server is immediately dropping the socket, try
                        # the alternate URL format on the next reconnect.
                        if len(urls) > 1:
                            self._ws_url_idx += 1
                            log.warning(
                                f"WS-{self.slot}: Switching WS URL variant and retrying on reconnect "
                                f"(idx={self._ws_url_idx % len(urls)})."
                            )
                        log.error(f"WS-{self.slot}: Server closed before subscribe — {exc}")
                        raise

                    # Re-subscribe all tokens assigned to this slot
                    try:
                        await self._resubscribe_all()
                    except Exception as exc:
                        # If the server is dropping the socket during subscribe,
                        # try a lower-bandwidth/entitlement request code.
                        msg = str(exc)
                        if (
                            "no close frame received" in msg
                            or "ConnectionClosed" in msg
                            or "received 100" in msg
                        ):
                            if self._downgrade_subscribe_rc():
                                log.warning(
                                    f"WS-{self.slot}: Subscribe failed; "
                                    f"downgrading RequestCode to {self._subscribe_rc} and retrying on reconnect."
                                )
                        log.error(f"WS-{self.slot}: Resubscribe failed — {exc}")
                        raise

                    async for message in ws:
                        if isinstance(message, bytes):
                            await self._handle_binary(message)
                        elif isinstance(message, str) and message.strip():
                            log.warning(f"WS-{self.slot}: Server text message: {message}")

            except ConnectionClosed as exc:
                log.warning(f"WS-{self.slot}: Connection closed — {exc}.")
                try:
                    await add_notification(
                        category="live_feed",
                        severity="warning",
                        title="Live feed connection closed",
                        message=f"WS-{self.slot}: {exc}",
                        dedupe_key=f"ws-{self.slot}-closed-{type(exc).__name__}",
                        dedupe_ttl_seconds=300,
                    )
                except Exception:
                    pass
            except asyncio.CancelledError:
                log.info(f"WS-{self.slot}: Cancelled.")
                return
            except Exception as exc:
                msg = str(exc)
                # Dhan sometimes rate-limits WS handshakes if we reconnect too quickly.
                # Treat this as a signal to cool down longer rather than hammering.
                if "HTTP 429" in msg:
                    rate_limited = True
                log.error(f"WS-{self.slot}: Error — {exc}")
                try:
                    await add_notification(
                        category="live_feed",
                        severity="error",
                        title="Dhan WebSocket data loop error",
                        message=f"WS-{self.slot}: {exc}",
                        dedupe_key=f"ws-{self.slot}-err-{type(exc).__name__}",
                        dedupe_ttl_seconds=240,
                    )
                except Exception:
                    pass
            finally:
                self._connected = False
                self._ws = None

            if rate_limited:
                await _trigger_global_cooldown()

            self._attempts += 1
            backoff = min(_BACKOFF_BASE * (2 ** (self._attempts - 1)), _BACKOFF_MAX)
            if rate_limited:
                backoff = max(backoff, 60)
                backoff += min(5, self.slot)  # small slot-based staggering
            log.info(f"WS-{self.slot}: Reconnecting in {backoff}s (attempt {self._attempts})…")
            await asyncio.sleep(backoff)

    async def _resubscribe_all(self) -> None:
        from app.instruments.subscription_manager import get_slot_tokens
        tokens = list(get_slot_tokens(self.slot))
        log.info(
            f"WS-{self.slot}: Subscribing {len(tokens)} tokens… "
            f"(RequestCode={self._subscribe_rc})"
        )
        await self._send_subscription(tokens, request_code=self._subscribe_rc)
        log.info(f"WS-{self.slot}: Re-subscribed {len(tokens)} tokens.")

    async def _log_initial_server_message(self) -> None:
        if not self._ws:
            return
        try:
            message = await asyncio.wait_for(self._ws.recv(), timeout=0.25)
        except asyncio.TimeoutError:
            return
        if isinstance(message, bytes):
            # Could be an early tick or control packet; parse it so we don't lose it.
            await self._handle_binary(message)
        elif isinstance(message, str) and message.strip():
            log.warning(f"WS-{self.slot}: Initial server text message: {message}")

    async def subscribe_tokens(self, tokens: list[int]) -> None:
        await self._send_subscription(tokens, request_code=self._subscribe_rc)

    async def unsubscribe_tokens(self, tokens: list[int]) -> None:
        if not self._ws or not self._connected:
            return
        for i in range(0, len(tokens), cfg.max_msg_instruments):
            chunk = tokens[i: i + cfg.max_msg_instruments]
            msg = {
                "RequestCode":     _RC_UNSUBSCRIBE,
                "InstrumentCount": len(chunk),
                "InstrumentList":  [{"SecurityId": str(t)} for t in chunk],
            }
            await self._ws.send(json.dumps(msg))

    async def _send_subscription(self, tokens: list[int], *, request_code: int) -> None:
        if not tokens or not self._ws or not self._connected:
            return
        # Fetch exchange segments for the token list
        from app.database import get_pool
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT instrument_token, exchange_segment FROM instrument_master "
            "WHERE instrument_token = ANY($1::bigint[])",
            tokens,
        )
        seg_map = {r["instrument_token"]: r["exchange_segment"] for r in rows}

        # Send in batches of 100
        for i in range(0, len(tokens), cfg.max_msg_instruments):
            chunk = tokens[i: i + cfg.max_msg_instruments]
            instrument_list = [
                {
                    "ExchangeSegment": seg_map.get(t, "NSE_FNO"),
                    "SecurityId":      str(t),
                }
                for t in chunk
            ]

            if _LIVE_FEED_EXCLUDE_SEGMENTS:
                before = len(instrument_list)
                instrument_list = [
                    it for it in instrument_list
                    if it.get("ExchangeSegment") not in _LIVE_FEED_EXCLUDE_SEGMENTS
                ]
                skipped = before - len(instrument_list)
                if skipped:
                    log.debug(
                        f"WS-{self.slot}: Skipping {skipped} instruments from subscribe payload "
                        f"due to excluded segment(s): {sorted(_LIVE_FEED_EXCLUDE_SEGMENTS)}"
                    )

            if not instrument_list:
                continue

            msg = {
                "RequestCode":     request_code,
                "InstrumentCount": len(instrument_list),
                "InstrumentList":  instrument_list,
            }
            try:
                await self._ws.send(json.dumps(msg))
            except Exception as exc:
                log.warning(
                    f"WS-{self.slot}: Subscribe send failed "
                    f"(batch={i//cfg.max_msg_instruments + 1}, size={len(chunk)}) — {exc}"
                )
                raise
            # Avoid bursting dozens of subscribe messages back-to-back.
            # A small delay dramatically reduces server-side throttling / 429s.
            await asyncio.sleep(0.05)

    async def _handle_binary(self, data: bytes) -> None:
        """
        Parse DhanHQ binary response packets.
        Header: [response_code(1), msg_len(2), exchange_segment(1), security_id(4)]
        Full Packet (code 8): LTP, LTQ, LTT, ATP, Volume, SellQ, BuyQ, OI,
                               OI_high, OI_low, Open, Close, High, Low + 5-level depth
        """
        if len(data) < 8:
            return

        response_code = data[0]
        # msg_len       = struct.unpack_from("<H", data, 1)[0]
        exchange_byte = data[3]
        security_id   = struct.unpack_from("<I", data, 4)[0]  # unsigned uint32

        if response_code == _FC_DISCONNECT:
            disc_code = struct.unpack_from("<H", data, 8)[0] if len(data) >= 10 else 0
            log.warning(f"WS-{self.slot}: Server disconnect packet — code {disc_code}")
            return

        if response_code not in (_FC_FULL, _FC_QUOTE, _FC_TICKER, _FC_PREV_CLOSE):
            return

        try:
            tick = _parse_full_packet(data, security_id, response_code)
            if tick:
                # Record exchange time sync from first tick
                if self._first_tick and tick.get("ltt"):
                    record_exchange_tick_time(tick["ltt"])
                    self._first_tick = False

                from app.market_data.tick_processor import tick_processor
                await tick_processor.push(tick)

        except Exception as exc:
            log.debug(f"WS-{self.slot}: Parse error for token {security_id} — {exc}")


def _parse_full_packet(data: bytes, security_id: int, code: int) -> dict | None:
    """
    Parse a Full Packet (code=8) or Quote Packet (code=4) from DhanHQ binary.
    Returns a dict with all fields needed for the market_data table.
    Returns None for unsupported codes.
    """
    if code == _FC_FULL and len(data) >= 162:
        # Full packet layout — struct '<BHBIfHIfIIIIIIffff100s' (162 bytes)
        # Offsets: B(1)+H(2)+B(1)+I(4)=8 → LTP(f,4), H(2) → LTQ, I(4) → LTT …
        ltp   = struct.unpack_from("<f", data, 8)[0]
        ltq   = struct.unpack_from("<H", data, 12)[0]  # unsigned short
        ltt_e = struct.unpack_from("<I", data, 14)[0]  # unsigned int (epoch)
        vol   = struct.unpack_from("<I", data, 22)[0]  # unsigned
        sellq = struct.unpack_from("<I", data, 26)[0]  # unsigned
        buyq  = struct.unpack_from("<I", data, 30)[0]  # unsigned
        oi    = struct.unpack_from("<I", data, 34)[0]  # unsigned
        open_ = struct.unpack_from("<f", data, 46)[0]
        close = struct.unpack_from("<f", data, 50)[0]
        high  = struct.unpack_from("<f", data, 54)[0]
        low   = struct.unpack_from("<f", data, 58)[0]

        # 5-level market depth: 5 × 20 bytes starting at offset 62
        bid_depth = []
        ask_depth = []
        for i in range(5):
            off = 62 + i * 20
            # depth level: '<IIHHff' = bid_qty(I) ask_qty(I) bid_ord(H) ask_ord(H) bid_px(f) ask_px(f)
            bid_qty   = struct.unpack_from("<I", data, off)[0]      # unsigned
            ask_qty   = struct.unpack_from("<I", data, off + 4)[0]  # unsigned
            bid_price = struct.unpack_from("<f", data, off + 12)[0]
            ask_price = struct.unpack_from("<f", data, off + 16)[0]
            bid_depth.append({"price": round(bid_price, 2), "qty": bid_qty})
            ask_depth.append({"price": round(ask_price, 2), "qty": ask_qty})

        ltt = datetime.fromtimestamp(ltt_e, tz=IST) if ltt_e else None

        return {
            "instrument_token": security_id,
            "ltp":   round(ltp, 2),
            "open":  round(open_, 2),
            "high":  round(high, 2),
            "low":   round(low, 2),
            "close": round(close, 2),
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "ltt": ltt,
        }

    elif code == _FC_TICKER and len(data) >= 16:
        # Ticker packet: '<BHBIfI' (16 bytes) — LTP at offset 8, LTT at offset 12
        ltp   = struct.unpack_from("<f", data, 8)[0]
        ltt_e = struct.unpack_from("<I", data, 12)[0]  # unsigned int (epoch)
        ltt   = datetime.fromtimestamp(ltt_e, tz=IST) if ltt_e else None
        return {
            "instrument_token": security_id,
            "ltp": round(ltp, 2),
            "ltt": ltt,
            # Other fields absent — tick_processor merges with existing row
        }

    return None


class _WebSocketManager:
    """Manages all 5 Live Market Feed WebSocket connections."""

    def __init__(self):
        self._conns = [_SingleWSConnection(i) for i in range(5)]

    def _active_slots(self) -> list[int]:
        """
        Return slots that currently have assigned tokens.
        Falls back to all slots if subscription state cannot be read.
        """
        try:
            from app.instruments.subscription_manager import slot_count

            active = [slot for slot in range(len(self._conns)) if slot_count(slot) > 0]
            return active
        except Exception:
            # Fail open to preserve stream availability if diagnostics fail.
            return list(range(len(self._conns)))

    async def start_all(self) -> None:
        if not is_nse_bse_ws_window_open_strict():
            await self.stop_all()
            log.info("WS manager: skipped startup (NSE/BSE market window is closed).")
            return

        active_slots = self._active_slots()
        if not active_slots:
            log.warning("WS manager: no active subscription slots found; skipping WS startup.")
            return

        for idx, conn in enumerate(self._conns):
            if idx in active_slots:
                await conn.start()
                await asyncio.sleep(2.0)   # light staggering; global gate handles the rest
            else:
                await conn.stop()
        log.info(f"WS manager: started slots {active_slots} (inactive slots stopped).")

    async def stop_all(self) -> None:
        for conn in self._conns:
            await conn.stop()
        log.info("All WebSocket connections stopped.")

    async def reconnect_all(self) -> None:
        """Token rotation — graceful reconnect of all connections."""
        active_slots = self._active_slots()
        if not active_slots:
            log.warning("WS manager: no active subscription slots; skipping reconnect.")
            return

        log.info(f"WS manager: reconnecting active slots {active_slots} (token rotation)...")
        for idx, conn in enumerate(self._conns):
            if idx in active_slots:
                await conn.reconnect()
                await asyncio.sleep(1.0)  # safer staggering for Dhan handshake limits
            else:
                await conn.stop()
        log.info("WS manager: active slot reconnect complete.")

    async def subscribe_tokens(self, slot: int, tokens: list[int]) -> None:
        if not is_nse_bse_ws_window_open_strict():
            return

        # Lazy-start slot connection on first demand; _resubscribe_all will replay
        # the full slot token-set once connected.
        conn = self._conns[slot]
        if conn._task is None or conn._task.done():
            await conn.start()
            await asyncio.sleep(0.1)
        await self._conns[slot].subscribe_tokens(tokens)

    async def unsubscribe_tokens(self, slot: int, tokens: list[int]) -> None:
        await self._conns[slot].unsubscribe_tokens(tokens)

    def connection_status(self) -> list[dict]:
        from app.instruments.subscription_manager import slot_count
        return [
            {
                "slot":      c.slot,
                "connected": c._connected,
                "tokens":    slot_count(c.slot),
                "capacity":  5000,
            }
            for c in self._conns
        ]

    def get_status(self) -> list[dict]:
        """Alias for connection_status — used by Admin router."""
        return self.connection_status()


# Singleton
ws_manager = _WebSocketManager()
