"""
app/routers/ws_feed.py
========================
WebSocket endpoint for real-time tick streaming to the frontend.
Clients subscribe / unsubscribe by sending JSON messages.

Connection flow:
  1. GET /ws/feed  → upgrade to WS
  2. Client sends: {"action": "subscribe",   "tokens": [123, 456]}
  3. Client sends: {"action": "unsubscribe", "tokens": [123]}
  4. Server pushes: {"type": "tick", "data": <serialize_tick output>}
  5. Server pushes: {"type": "pong"} in response to {"action": "ping"}
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from app.websocket_push import ws_push
import app.instruments.subscription_manager as subscription_manager
from app.database           import get_pool
from app.serializers.market_data import serialize_tick
from app.market_hours import IST, is_equity_window_active, is_commodity_window_active
from app.instruments.atm_calculator import get_underlying_price

router = APIRouter(prefix="/ws", tags=["WebSocket Feed"])
log    = logging.getLogger(__name__)

DEFAULT_USER_ID = "default"   # TODO: replace with auth guard


# Shared prices snapshot cache for /ws/prices to avoid per-connection heavy DB reads.
_prices_cache_lock = asyncio.Lock()
_prices_cache_payload: dict | None = None
_prices_cache_updated_monotonic: float = 0.0
_PRICES_CACHE_TTL_SECONDS = 2.0


async def _get_prices_payload_cached() -> dict:
    """Build prices payload at most once per TTL and share across connections."""
    global _prices_cache_payload, _prices_cache_updated_monotonic

    now_mono = time.monotonic()
    if (
        _prices_cache_payload is not None
        and (now_mono - _prices_cache_updated_monotonic) < _PRICES_CACHE_TTL_SECONDS
    ):
        return _prices_cache_payload

    async with _prices_cache_lock:
        now_mono = time.monotonic()
        if (
            _prices_cache_payload is not None
            and (now_mono - _prices_cache_updated_monotonic) < _PRICES_CACHE_TTL_SECONDS
        ):
            return _prices_cache_payload

        pool = get_pool()

        now_ist = datetime.now(tz=IST)
        market_active_equity = is_equity_window_active(now_ist)
        market_active_commodity = is_commodity_window_active(now_ist)
        market_active = market_active_equity or market_active_commodity

        core_underlyings = ["NIFTY", "BANKNIFTY", "CRUDEOIL", "RELIANCE"]
        core_rows = await pool.fetch(
            """
            SELECT DISTINCT ON (im.underlying)
                im.underlying,
                im.instrument_type,
                md.ltp,
                md.close,
                md.updated_at,
                im.expiry_date
            FROM instrument_master im
            JOIN market_data md ON md.instrument_token = im.instrument_token
            WHERE im.underlying = ANY($1::text[])
              AND im.instrument_type = ANY($2::text[])
              AND (md.ltp IS NOT NULL OR md.close IS NOT NULL)
            ORDER BY
                im.underlying,
                CASE WHEN im.instrument_type = 'INDEX' THEN 0 ELSE 1 END,
                (im.expiry_date >= CURRENT_DATE) DESC,
                im.expiry_date NULLS LAST,
                md.updated_at DESC
            """,
            core_underlyings,
            ["INDEX", "FUTIDX", "FUTSTK", "FUTCOM"],
        )
        prices: dict[str, float] = {}
        for r in core_rows:
            val = r["ltp"] if r["ltp"] is not None else r["close"]
            if val is not None:
                prices[r["underlying"]] = float(val)

        # Fallback for index underlyings: use cached spot from optionchain poller
        # when market_data has temporary gaps (commonly visible for SENSEX).
        for ul in ("NIFTY", "BANKNIFTY", "SENSEX"):
            if ul in prices:
                continue
            try:
                spot = get_underlying_price(ul)
                if spot is not None and float(spot) > 0:
                    prices[ul] = float(spot)
            except Exception:
                pass

        wl_tokens = await pool.fetch(
            "SELECT DISTINCT instrument_token FROM watchlist_items WHERE instrument_token IS NOT NULL"
        )
        wl_ids = [int(r["instrument_token"]) for r in wl_tokens if r.get("instrument_token")]
        if wl_ids:
            wl_rows = await pool.fetch(
                """
                SELECT md.instrument_token, md.ltp, md.close, im.symbol
                FROM market_data md
                LEFT JOIN instrument_master im ON im.instrument_token = md.instrument_token
                WHERE md.instrument_token = ANY($1::bigint[])
                """,
                wl_ids,
            )
            for r in wl_rows:
                val = r["ltp"] if r["ltp"] is not None else r["close"]
                if val is None:
                    continue
                prices[str(r["instrument_token"])] = float(val)
                if r["symbol"]:
                    prices[r["symbol"]] = float(val)

        # Ensure all currently open position tokens are present in the pulse map.
        # Positions UI resolves LTP primarily via token/symbol keys from this payload.
        open_pos_tokens = await pool.fetch(
            """
            SELECT DISTINCT instrument_token
            FROM paper_positions
            WHERE instrument_token IS NOT NULL
              AND status = 'OPEN'
              AND COALESCE(quantity, 0) <> 0
            """
        )
        open_pos_ids = [
            int(r["instrument_token"])
            for r in open_pos_tokens
            if r.get("instrument_token")
        ]
        if open_pos_ids:
            pos_rows = await pool.fetch(
                """
                SELECT md.instrument_token, md.ltp, md.close, im.symbol
                FROM market_data md
                LEFT JOIN instrument_master im ON im.instrument_token = md.instrument_token
                WHERE md.instrument_token = ANY($1::bigint[])
                """,
                open_pos_ids,
            )
            for r in pos_rows:
                val = r["ltp"] if r["ltp"] is not None else r["close"]
                if val is None:
                    continue
                prices[str(r["instrument_token"])] = float(val)
                if r["symbol"]:
                    prices[r["symbol"]] = float(val)

        rows = await pool.fetch(
            """
            SELECT instrument_token, ltp, close, symbol
            FROM market_data
            WHERE ltp IS NOT NULL OR close IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT 400
            """
        )
        for r in rows:
            val = r["ltp"] if r["ltp"] is not None else r["close"]
            if val is None:
                continue
            prices.setdefault(str(r["instrument_token"]), float(val))
            if r["symbol"]:
                prices.setdefault(r["symbol"], float(val))

        _prices_cache_payload = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "status": "active",
            "market_active": market_active,
            "market_active_equity": market_active_equity,
            "market_active_commodity": market_active_commodity,
            "prices": prices,
        }
        _prices_cache_updated_monotonic = time.monotonic()
        return _prices_cache_payload


@router.websocket("/prices")
async def websocket_prices(ws: WebSocket):
    """Broadcast all market prices — used by useMarketPulse hook."""
    await ws_push.connect(ws, DEFAULT_USER_ID)
    try:
        while True:
            # Per-connection send interval can stay responsive while DB reads are cached.
            await asyncio.sleep(0.5)
            payload = await _get_prices_payload_cached()
            await ws.send_text(json.dumps(payload))
    except WebSocketDisconnect:
        await ws_push.disconnect(ws, DEFAULT_USER_ID)
    except Exception:
        await ws_push.disconnect(ws, DEFAULT_USER_ID)


@router.websocket("/feed")
async def websocket_feed(ws: WebSocket):
    await ws_push.connect(ws, DEFAULT_USER_ID)
    try:
        while True:
            try:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                    continue

                action = msg.get("action")

                if action == "subscribe":
                    tokens = [int(t) for t in msg.get("tokens", []) if str(t).strip()]
                    if not tokens:
                        continue
                    # Request Tier-A subscriptions for any that aren't already live,
                    # but never block snapshot delivery to the UI.
                    sub_tasks = [subscription_manager.subscribe_tier_a(token) for token in tokens]
                    if sub_tasks:
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*sub_tasks, return_exceptions=True),
                                timeout=1.5,
                            )
                        except asyncio.TimeoutError:
                            log.warning("/ws/feed subscribe_tier_a timed out; proceeding with snapshot")

                    await ws_push.subscribe(ws, tokens)

                    # Send immediate snapshot for all requested tokens.
                    pool = get_pool()
                    rows = await pool.fetch(
                        """
                        SELECT md.*, im.exchange_segment
                        FROM market_data md
                        JOIN instrument_master im ON im.instrument_token = md.instrument_token
                        WHERE md.instrument_token = ANY($1::bigint[])
                        """,
                        tokens,
                    )
                    snapshots = []
                    for r in rows:
                        d = dict(r)
                        try:
                            snapshots.append(
                                serialize_tick(
                                    d,
                                    d.get("exchange_segment") or "NSE_FNO",
                                    d.get("symbol") or "",
                                    include_depth_qty=True,
                                    depth_levels=5,
                                )
                            )
                        except Exception as exc:
                            log.warning("/ws/feed snapshot serialize failed for token=%s: %s", d.get("instrument_token"), exc)
                    await ws.send_text(json.dumps({"type": "snapshot", "data": snapshots}))

                elif action == "unsubscribe":
                    tokens = [int(t) for t in msg.get("tokens", []) if str(t).strip()]
                    await ws_push.unsubscribe(ws, tokens)

                elif action == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))

                else:
                    await ws.send_text(json.dumps({"type": "error", "message": f"Unknown action: {action}"}))
            except Exception as exc:
                # Keep connection alive for transient handler issues.
                log.error("/ws/feed handler error: %s", exc)
                try:
                    await ws.send_text(json.dumps({"type": "error", "message": "internal handler error"}))
                except Exception:
                    break

    except WebSocketDisconnect:
        pass
    finally:
        await ws_push.disconnect(ws, DEFAULT_USER_ID)
