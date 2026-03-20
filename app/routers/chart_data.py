"""
app/routers/chart_data.py
=========================
Chart-data endpoints for external chart UI integration.

Contract goals:
- Backend-owned instrument resolution
- Authenticated history/quotes/lookup/websocket
- No synthetic candle fabrication
"""
from __future__ import annotations

import json
import logging
import asyncio
import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import get_pool
from app.dependencies import CurrentUser, get_current_user
from app.market_data.rate_limiter import dhan_client


log = logging.getLogger(__name__)
cfg = get_settings()

router = APIRouter(tags=["Chart Data"])


SUPPORTED_INTERVALS = {"1m", "3m", "5m", "15m", "25m", "30m", "60m", "75m", "D"}

# Head gap fills larger than this are skipped to avoid blocking on massive DhanHQ request loops.
MAX_HEAD_GAP_FILL_MS = 90 * 24 * 60 * 60 * 1000  # 90 days in ms

# Derived intervals are built from real lower-timeframe candles only.
DERIVED_BASE_INTERVAL = {
    "3m": "1m",
    "30m": "5m",
    "75m": "15m",
}

UPSTREAM_INTERVAL = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "25m": "25",
    "60m": "60",
    "D": "D",
}


def _error_payload(
    code: str,
    message: str,
    *,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        },
    }


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=_error_payload(code, message, retryable=retryable, details=details),
    )


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _precision_from_tick_size(tick_size: Any) -> int:
    if tick_size in (None, "", 0):
        return 2
    try:
        dec = Decimal(str(tick_size)).normalize()
        if dec.as_tuple().exponent >= 0:
            return 0
        return abs(dec.as_tuple().exponent)
    except Exception:
        return 2


def _to_epoch_ms(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    if isinstance(value, (int, float)):
        num = float(value)
        if num > 10_000_000_000:  # likely ms
            return int(num)
        if num > 1_000_000_000:   # likely sec
            return int(num * 1000)
        return None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            return _to_epoch_ms(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    return None


async def _resolve_instrument(
    *,
    instrument_id: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any] | None:
    pool = get_pool()

    iid = (instrument_id or "").strip()
    sym = (symbol or "").strip()

    if iid:
        if not iid.isdigit():
            return None
        row = await pool.fetchrow(
            """
            SELECT instrument_token, security_id, symbol, display_name, exchange_segment, instrument_type, tick_size, lot_size
            FROM instrument_master
            WHERE instrument_token = $1::bigint OR security_id = $1::bigint
            LIMIT 1
            """,
            int(iid),
        )
        return dict(row) if row else None

    if sym:
        q = sym.upper()
        row = await pool.fetchrow(
            """
            SELECT instrument_token, security_id, symbol, display_name, exchange_segment, instrument_type, tick_size, lot_size
            FROM instrument_master
            WHERE upper(symbol) = $1
               OR upper(COALESCE(display_name, '')) = $1
               OR CAST(security_id AS text) = $2
            ORDER BY
              CASE
                WHEN upper(symbol) = $1 THEN 0
                WHEN upper(COALESCE(display_name, '')) = $1 THEN 1
                ELSE 2
              END,
                            CASE
                                WHEN exchange_segment = 'NSE_EQ' AND instrument_type = 'EQUITY' THEN 0
                                WHEN exchange_segment = 'BSE_EQ' AND instrument_type = 'EQUITY' THEN 1
                                ELSE 2
                            END,
                            length(symbol),
              instrument_token
            LIMIT 1
            """,
            q,
            sym,
        )
        return dict(row) if row else None

    return None


def _parse_candle_row(item: Any) -> dict[str, Any] | None:
    ts: int | None = None
    o = h = l = c = v = None

    if isinstance(item, (list, tuple)) and len(item) >= 6:
        ts = _to_epoch_ms(item[0])
        o = _to_float(item[1])
        h = _to_float(item[2])
        l = _to_float(item[3])
        c = _to_float(item[4])
        v = _to_float(item[5])
    elif isinstance(item, dict):
        ts = _to_epoch_ms(
            item.get("timestamp")
            or item.get("time")
            or item.get("datetime")
            or item.get("date")
        )
        o = _to_float(item.get("open") or item.get("o"))
        h = _to_float(item.get("high") or item.get("h"))
        l = _to_float(item.get("low") or item.get("l"))
        c = _to_float(item.get("close") or item.get("c"))
        v = _to_float(item.get("volume") or item.get("v"))

    if ts is None or o is None or h is None or l is None or c is None:
        return None

    return {
        "timestamp": int(ts),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
    }


def _extract_candle_rows(payload: Any) -> list[dict[str, Any]]:
    # Official Dhan historical format: parallel arrays keyed by OHLC fields.
    if isinstance(payload, dict):
        arr_ts = payload.get("timestamp")
        arr_o = payload.get("open")
        arr_h = payload.get("high")
        arr_l = payload.get("low")
        arr_c = payload.get("close")
        arr_v = payload.get("volume")

        if all(isinstance(x, list) for x in (arr_ts, arr_o, arr_h, arr_l, arr_c)):
            n = min(len(arr_ts), len(arr_o), len(arr_h), len(arr_l), len(arr_c))
            rows: list[dict[str, Any]] = []
            for i in range(n):
                ts_ms = _to_epoch_ms(arr_ts[i])
                if ts_ms is None:
                    continue
                parsed = {
                    "timestamp": int(ts_ms),
                    "open": _to_float(arr_o[i]),
                    "high": _to_float(arr_h[i]),
                    "low": _to_float(arr_l[i]),
                    "close": _to_float(arr_c[i]),
                    "volume": _to_float(arr_v[i]) if isinstance(arr_v, list) and i < len(arr_v) else None,
                }
                if None in (parsed["open"], parsed["high"], parsed["low"], parsed["close"]):
                    continue
                rows.append(parsed)
            return rows

    candidates: Any = payload
    if isinstance(payload, dict):
        candidates = payload.get("data", payload)
    if isinstance(candidates, dict):
        candidates = (
            candidates.get("candles")
            or candidates.get("bars")
            or candidates.get("ohlc")
            or candidates.get("items")
            or []
        )

    if not isinstance(candidates, list):
        return []

    rows: list[dict[str, Any]] = []
    for item in candidates:
        parsed = _parse_candle_row(item)
        if parsed:
            rows.append(parsed)
    return rows


def _aggregate_candles(candles: list[dict[str, Any]], interval: str) -> list[dict[str, Any]]:
    if not candles:
        return []

    minute_map = {"3m": 3, "30m": 30, "75m": 75}
    if interval in minute_map:
        bucket_ms = minute_map[interval] * 60 * 1000

        buckets: dict[int, dict[str, Any]] = {}
        for candle in candles:
            ts = int(candle["timestamp"])
            bucket_ts = (ts // bucket_ms) * bucket_ms

            if bucket_ts not in buckets:
                buckets[bucket_ts] = {
                    "timestamp": bucket_ts,
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle.get("volume") or 0.0,
                }
                continue

            b = buckets[bucket_ts]
            b["high"] = max(float(b["high"]), float(candle["high"]))
            b["low"] = min(float(b["low"]), float(candle["low"]))
            b["close"] = candle["close"]
            b["volume"] = float(b.get("volume") or 0.0) + float(candle.get("volume") or 0.0)

        return [buckets[k] for k in sorted(buckets.keys())]

    return candles


def _apply_history_filters(
    candles: list[dict[str, Any]],
    *,
    from_ts: int | None,
    to_ts: int | None,
    countback: int | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    out = candles
    if from_ts is not None:
        out = [c for c in out if int(c["timestamp"]) >= from_ts]
    if to_ts is not None:
        out = [c for c in out if int(c["timestamp"]) <= to_ts]

    # Deduplicate by timestamp and enforce ascending order.
    dedup: dict[int, dict[str, Any]] = {int(c["timestamp"]): c for c in out}
    out = [dedup[k] for k in sorted(dedup.keys())]

    if countback is not None and countback > 0:
        out = out[-countback:]
    if limit is not None and limit > 0:
        out = out[-limit:]
    return out


async def _fetch_upstream_candles(
    *,
    security_id: int,
    exchange_segment: str,
    instrument_type: str | None,
    interval: str,
    from_ts: int | None,
    to_ts: int | None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    if cfg.dhan_disabled:
        return None, "Dhan connectivity disabled"

    upstream_interval = UPSTREAM_INTERVAL.get(interval, interval)
    from_dt = datetime.fromtimestamp(from_ts / 1000, tz=timezone.utc) if from_ts else None
    to_dt = datetime.fromtimestamp(to_ts / 1000, tz=timezone.utc) if to_ts else None

    inst = (instrument_type or "").strip().upper() or "EQUITY"

    async def _fetch_once(path: str, payload: dict[str, Any]) -> tuple[list[dict[str, Any]] | None, str | None]:
        try:
            resp = await dhan_client.post(path, json=payload)
        except Exception as exc:
            log.warning("chart history upstream call failure on %s: %s", path, exc)
            return None, "UPSTREAM_REQUEST_FAILED"

        if resp.status_code == 404:
            return None, "UPSTREAM_ENDPOINT_UNAVAILABLE"
        if resp.status_code >= 500:
            return None, "UPSTREAM_SERVER_ERROR"
        if resp.status_code >= 400:
            log.warning("chart history upstream returned status=%s on %s", resp.status_code, path)
            return None, "UPSTREAM_BAD_RESPONSE"

        try:
            parsed_payload = resp.json()
        except Exception:
            parsed_payload = None

        return _extract_candle_rows(parsed_payload), None

    # Dhan docs: intraday endpoint supports up to 90 days per request.
    if upstream_interval == "D":
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": inst,
            "oi": False,
            "fromDate": from_dt.strftime("%Y-%m-%d") if from_dt else None,
            "toDate": to_dt.strftime("%Y-%m-%d") if to_dt else None,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return await _fetch_once("/charts/historical", payload)

    start_dt = from_dt or (datetime.now(tz=timezone.utc) - timedelta(days=30))
    end_dt = to_dt or datetime.now(tz=timezone.utc)
    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    all_rows: list[dict[str, Any]] = []
    window_start = start_dt
    while window_start <= end_dt:
        window_end = min(window_start + timedelta(days=90), end_dt)
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": inst,
            "interval": upstream_interval,
            "oi": False,
            "fromDate": window_start.strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": window_end.strftime("%Y-%m-%d %H:%M:%S"),
        }
        rows, err = await _fetch_once("/charts/intraday", payload)
        if rows is None:
            return None, err
        all_rows.extend(rows)

        if window_end >= end_dt:
            break
        window_start = window_end + timedelta(seconds=1)

    dedup = {int(c["timestamp"]): c for c in all_rows}
    ordered = [dedup[k] for k in sorted(dedup.keys())]
    return ordered, None


# ── DB Candle Cache Helpers ────────────────────────────────────────────────

async def _db_get_coverage(
    pool, security_id: int, exchange_segment: str, interval: str
) -> tuple[int | None, int | None]:
    """Return (min_ts, max_ts) of candles stored in DB, or (None, None) if none."""
    row = await pool.fetchrow(
        "SELECT min_ts, max_ts FROM chart_candles_coverage "
        "WHERE security_id=$1 AND exchange_segment=$2 AND interval=$3",
        security_id, exchange_segment, interval,
    )
    if row:
        return int(row["min_ts"]), int(row["max_ts"])
    return None, None


async def _db_load_candles(
    pool,
    security_id: int,
    exchange_segment: str,
    interval: str,
    from_ts: int | None,
    to_ts: int | None,
) -> list[dict[str, Any]]:
    """Load OHLCV rows from the persistent candle cache."""
    if from_ts is not None and to_ts is not None:
        rows = await pool.fetch(
            "SELECT ts, open, high, low, close, volume FROM chart_candles "
            "WHERE security_id=$1 AND exchange_segment=$2 AND interval=$3 "
            "  AND ts >= $4 AND ts <= $5 ORDER BY ts",
            security_id, exchange_segment, interval, from_ts, to_ts,
        )
    elif from_ts is not None:
        rows = await pool.fetch(
            "SELECT ts, open, high, low, close, volume FROM chart_candles "
            "WHERE security_id=$1 AND exchange_segment=$2 AND interval=$3 "
            "  AND ts >= $4 ORDER BY ts",
            security_id, exchange_segment, interval, from_ts,
        )
    elif to_ts is not None:
        rows = await pool.fetch(
            "SELECT ts, open, high, low, close, volume FROM chart_candles "
            "WHERE security_id=$1 AND exchange_segment=$2 AND interval=$3 "
            "  AND ts <= $4 ORDER BY ts",
            security_id, exchange_segment, interval, to_ts,
        )
    else:
        rows = await pool.fetch(
            "SELECT ts, open, high, low, close, volume FROM chart_candles "
            "WHERE security_id=$1 AND exchange_segment=$2 AND interval=$3 ORDER BY ts",
            security_id, exchange_segment, interval,
        )
    return [
        {
            "timestamp": int(r["ts"]),
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
        }
        for r in rows
    ]


async def _db_save_candles(
    pool,
    security_id: int,
    exchange_segment: str,
    interval: str,
    candles: list[dict[str, Any]],
) -> None:
    """Bulk-upsert candles into DB and update the coverage table."""
    if not candles:
        return
    ts_list     = [int(c["timestamp"]) for c in candles]
    open_list   = [float(c["open"]) if c["open"] is not None else None for c in candles]
    high_list   = [float(c["high"]) if c["high"] is not None else None for c in candles]
    low_list    = [float(c["low"]) if c["low"] is not None else None for c in candles]
    close_list  = [float(c["close"]) if c["close"] is not None else None for c in candles]
    volume_list = [float(c["volume"]) if c.get("volume") is not None else None for c in candles]

    await pool.execute(
        """
        INSERT INTO chart_candles
            (security_id, exchange_segment, interval, ts, open, high, low, close, volume)
        SELECT $1, $2, $3,
               unnest($4::bigint[]),
               unnest($5::float8[]),
               unnest($6::float8[]),
               unnest($7::float8[]),
               unnest($8::float8[]),
               unnest($9::float8[])
        ON CONFLICT (security_id, exchange_segment, interval, ts) DO NOTHING
        """,
        security_id, exchange_segment, interval,
        ts_list, open_list, high_list, low_list, close_list, volume_list,
    )
    min_ts = min(ts_list)
    max_ts = max(ts_list)
    await pool.execute(
        """
        INSERT INTO chart_candles_coverage
            (security_id, exchange_segment, interval, min_ts, max_ts, updated_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
        ON CONFLICT (security_id, exchange_segment, interval) DO UPDATE
            SET min_ts     = LEAST(chart_candles_coverage.min_ts, EXCLUDED.min_ts),
                max_ts     = GREATEST(chart_candles_coverage.max_ts, EXCLUDED.max_ts),
                updated_at = NOW()
        """,
        security_id, exchange_segment, interval, min_ts, max_ts,
    )


@router.get("/chart/history")
async def chart_history(
    instrument_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    interval: str = Query(...),
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    countback: int | None = Query(default=None, ge=1, le=5000),
    limit: int | None = Query(default=None, ge=1, le=5000),
    _user: CurrentUser = Depends(get_current_user),
):
    if not (instrument_id or symbol):
        return _error_response(
            400,
            "INVALID_REQUEST",
            "Either instrument_id or symbol is required.",
            details={"required_any_of": ["instrument_id", "symbol"]},
        )

    norm_interval = (interval or "").strip()
    if norm_interval not in SUPPORTED_INTERVALS:
        return _error_response(
            422,
            "UNSUPPORTED_INTERVAL",
            "Unsupported interval.",
            details={"supported_intervals": sorted(SUPPORTED_INTERVALS)},
        )

    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        return _error_response(
            400,
            "INVALID_REQUEST",
            "`from` must be less than or equal to `to`.",
        )

    resolved = await _resolve_instrument(instrument_id=instrument_id, symbol=symbol)
    if not resolved:
        return _error_response(
            404,
            "INVALID_SYMBOL",
            "Instrument could not be resolved.",
            details={"instrument_id": instrument_id, "symbol": symbol},
        )

    effective_interval = norm_interval
    upstream_interval = norm_interval
    if norm_interval in DERIVED_BASE_INTERVAL:
        upstream_interval = DERIVED_BASE_INTERVAL[norm_interval]

    pool = get_pool()
    sec_id        = int(resolved["security_id"])
    exch_seg      = str(resolved["exchange_segment"])
    inst_type     = str(resolved.get("instrument_type") or "")
    now_ms        = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    req_to        = to_ts if to_ts is not None else now_ms

    db_min_ts, db_max_ts = await _db_get_coverage(pool, sec_id, exch_seg, upstream_interval)

    if db_max_ts is not None:
        # --- Cache exists: fetch only the gaps then serve from DB ---

        # Gap at the tail (newer data than what the cache holds)
        if req_to > db_max_ts:
            gap_from = db_max_ts + 1
            gap_to   = req_to
            log.info(
                "[chart/history] Filling tail gap %s %s %s: %s → %s",
                sec_id, exch_seg, upstream_interval, gap_from, gap_to,
            )
            new_rows, _ = await _fetch_upstream_candles(
                security_id=sec_id,
                exchange_segment=exch_seg,
                instrument_type=inst_type,
                interval=upstream_interval,
                from_ts=gap_from,
                to_ts=gap_to,
            )
            if new_rows:
                await _db_save_candles(pool, sec_id, exch_seg, upstream_interval, new_rows)

        # Gap at the head (older data requested than what cache holds)
        if from_ts is not None and db_min_ts is not None and from_ts < db_min_ts:
            head_gap_ms = db_min_ts - from_ts
            if head_gap_ms > MAX_HEAD_GAP_FILL_MS:
                log.info(
                    "[chart/history] Head gap %d days exceeds cap — skipping fill, serving cache",
                    head_gap_ms // 86_400_000,
                )
            else:
                gap_from = from_ts
                gap_to   = db_min_ts - 1
                log.info(
                    "[chart/history] Filling head gap %s %s %s: %s → %s",
                    sec_id, exch_seg, upstream_interval, gap_from, gap_to,
                )
                head_rows, _ = await _fetch_upstream_candles(
                    security_id=sec_id,
                    exchange_segment=exch_seg,
                    instrument_type=inst_type,
                    interval=upstream_interval,
                    from_ts=gap_from,
                    to_ts=gap_to,
                )
                if head_rows:
                    await _db_save_candles(pool, sec_id, exch_seg, upstream_interval, head_rows)

        raw_rows = await _db_load_candles(
            pool, sec_id, exch_seg, upstream_interval, from_ts, req_to if to_ts is not None else None
        )
        upstream_error = None

    else:
        # --- No cache: fetch everything from DhanHQ and persist ---
        log.info(
            "[chart/history] No cache for %s %s %s — fetching from upstream",
            sec_id, exch_seg, upstream_interval,
        )
        raw_rows, upstream_error = await _fetch_upstream_candles(
            security_id=sec_id,
            exchange_segment=exch_seg,
            instrument_type=inst_type,
            interval=upstream_interval,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        if raw_rows is None:
            return _error_response(
                503,
                "UPSTREAM_UNAVAILABLE",
                "Failed to fetch historical candles from upstream.",
                retryable=True,
                details={"reason": upstream_error or "unknown"},
            )
        if raw_rows:
            await _db_save_candles(pool, sec_id, exch_seg, upstream_interval, raw_rows)

    candles = raw_rows
    if effective_interval in DERIVED_BASE_INTERVAL:
        candles = _aggregate_candles(candles, effective_interval)

    candles = _apply_history_filters(
        candles,
        from_ts=from_ts,
        to_ts=to_ts,
        countback=countback,
        limit=limit,
    )

    payload = {
        "ok": True,
        "data": {
            "instrument_id": str(resolved["instrument_token"]),
            "interval": effective_interval,
            "timestamp_unit": "ms",
            "candles": candles,
        },
    }
    if not candles:
        payload["meta"] = {
            "no_data": True,
            "reason": "NO_REAL_CANDLES_IN_RANGE",
        }
    return payload


@router.get("/chart/quotes")
async def chart_quotes(
    instrument_ids: str | None = Query(default=None),
    symbols: str | None = Query(default=None),
    _user: CurrentUser = Depends(get_current_user),
):
    if not (instrument_ids or symbols):
        return _error_response(
            400,
            "INVALID_REQUEST",
            "Either instrument_ids or symbols must be provided.",
        )

    requested_tokens: set[int] = set()
    unresolved: list[dict[str, Any]] = []

    pool = get_pool()

    if instrument_ids:
        for raw in [x.strip() for x in instrument_ids.split(",") if x.strip()]:
            if not raw.isdigit():
                unresolved.append({"key": raw, "code": "INVALID_SYMBOL"})
                continue
            resolved = await _resolve_instrument(instrument_id=raw)
            if not resolved:
                unresolved.append({"key": raw, "code": "INVALID_SYMBOL"})
                continue
            requested_tokens.add(int(resolved["instrument_token"]))

    if symbols:
        for raw in [x.strip() for x in symbols.split(",") if x.strip()]:
            resolved = await _resolve_instrument(symbol=raw)
            if not resolved:
                unresolved.append({"key": raw, "code": "INVALID_SYMBOL"})
                continue
            requested_tokens.add(int(resolved["instrument_token"]))

    rows = []
    if requested_tokens:
        rows = await pool.fetch(
            """
            SELECT im.instrument_token,
                   im.symbol,
                   im.display_name,
                   im.exchange_segment,
                   md.updated_at,
                   md.ltt,
                   md.ltp,
                   md.open,
                   md.high,
                   md.low,
                   md.close
            FROM instrument_master im
            LEFT JOIN market_data md ON md.instrument_token = im.instrument_token
            WHERE im.instrument_token = ANY($1::bigint[])
            ORDER BY im.instrument_token
            """,
            list(requested_tokens),
        )

    data: list[dict[str, Any]] = []
    for r in rows:
        ts = _to_epoch_ms(r.get("ltt")) or _to_epoch_ms(r.get("updated_at"))
        data.append(
            {
                "instrument_id": str(r["instrument_token"]),
                "symbol": (r.get("display_name") or r.get("symbol") or ""),
                "exchange": r.get("exchange_segment"),
                "timestamp": ts,
                "last_price": _to_float(r.get("ltp")),
                "open": _to_float(r.get("open")),
                "high": _to_float(r.get("high")),
                "low": _to_float(r.get("low")),
                "close": _to_float(r.get("close")),
                "previous_close": _to_float(r.get("close")),
                "volume": None,
                "is_stale": (ts is None),
            }
        )

    out: dict[str, Any] = {
        "ok": True,
        "data": data,
        "timestamp_unit": "ms",
    }

    if not data:
        out["meta"] = {
            "no_data": True,
            "reason": "NO_QUOTE_AVAILABLE",
        }

    if unresolved:
        out.setdefault("meta", {})
        out["meta"]["unresolved"] = unresolved

    return out


@router.get("/chart/instruments")
async def chart_instruments(
    query: str | None = Query(default=None, min_length=1),
    tier: str | None = Query(default=None, regex="^[AB]$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    exchange: str | None = Query(default=None),
    _user: CurrentUser = Depends(get_current_user),
):
    """
    Search for instruments for charting.
    
    - If `query` is provided: search by symbol/display_name/security_id
    - If `tier` is provided (A or B): filter by tier (useful for browsing all instruments)
    - If both `query` and `tier` are provided: search within the tier
    - If neither: return empty (search is required or tier must be specified)
    """
    pool = get_pool()

    has_trading_symbol = bool(await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'instrument_master'
              AND column_name = 'trading_symbol'
        )
        """
    ))
    has_underlying = bool(await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'instrument_master'
              AND column_name = 'underlying'
        )
        """
    ))
    has_option_type = bool(await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'instrument_master'
              AND column_name = 'option_type'
        )
        """
    ))
    has_strike_price = bool(await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'instrument_master'
              AND column_name = 'strike_price'
        )
        """
    ))
    
    # If no query and no tier, require one
    if not query and not tier:
        return _error_response(
            400,
            "INVALID_REQUEST",
            "Either 'query' (search) or 'tier' (A or B) is required.",
        )
    
    q = (query or "").strip() if query else ""
    q_upper = q.upper()
    raw_tokens = [t for t in re.findall(r"[A-Za-z0-9]+", q_upper) if t]
    search_tokens = [
        ("CE" if t == "CALL" else "PE" if t == "PUT" else t)
        for t in raw_tokens
    ]
    if not search_tokens and q_upper:
        search_tokens = [q_upper]

    option_intent = any(t in ("CE", "PE") for t in search_tokens) or any(
        t.isdigit() and len(t) >= 3 for t in search_tokens
    )
    
    # Build WHERE clause
    where_conditions = []
    params = []
    param_index = 1
    
    # Search by symbol/name if query provided
    q_exact_ref = None
    strike_exact_ref = None

    if q:
        q_exact_ref = f"${param_index}"
        params.append(q)
        param_index += 1

        per_token_clauses = []
        for token in search_tokens:
            token_like_ref = f"${param_index}"
            params.append(f"%{token}%")
            param_index += 1

            token_exact_ref = f"${param_index}"
            params.append(token)
            param_index += 1

            if strike_exact_ref is None and token.isdigit() and len(token) >= 3:
                strike_exact_ref = token_exact_ref

            token_conditions = [
                f"upper(symbol) ILIKE {token_like_ref}",
                f"upper(COALESCE(display_name, '')) ILIKE {token_like_ref}",
                f"CAST(security_id AS text) = {token_exact_ref}",
                f"CAST(instrument_token AS text) = {token_exact_ref}",
            ]
            if has_trading_symbol:
                token_conditions.append(f"upper(COALESCE(trading_symbol, '')) ILIKE {token_like_ref}")
            if has_underlying:
                token_conditions.append(f"upper(COALESCE(underlying, '')) ILIKE {token_like_ref}")
            if has_option_type:
                token_conditions.append(f"upper(COALESCE(option_type, '')) = {token_exact_ref}")
            if has_strike_price:
                token_conditions.append(f"CAST(strike_price AS text) ILIKE {token_like_ref}")

            per_token_clauses.append(
                f"""(
                        {" OR ".join(token_conditions)}
                )"""
            )

        where_conditions.append("(" + " AND ".join(per_token_clauses) + ")")
    
    # Filter by tier if provided
    if tier:
        where_conditions.append(f"tier = ${param_index}")
        params.append(tier)
        param_index += 1
    
    # Filter by exchange if provided
    if exchange:
        where_conditions.append(f"exchange_segment = ${param_index}")
        params.append(exchange)
        param_index += 1
    
    where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
    
    # Add limit/offset
    params.append(limit)
    params.append(offset)
    limit_offset_clause = f"LIMIT ${param_index} OFFSET ${param_index + 1}"
    
    # Determine ORDER BY (prefer exact matches and NSE_EQ equities if no query)
    order_by = (
        f"""
        ORDER BY
            CASE
                WHEN CAST(instrument_token AS text) = {q_exact_ref} THEN -2
                WHEN CAST(security_id AS text) = {q_exact_ref} THEN -1
                WHEN {str(option_intent).upper()} AND instrument_type LIKE 'OPT%' THEN 0
                WHEN {str(option_intent).upper()} AND instrument_type LIKE 'FUT%' THEN 1
                WHEN {str(option_intent).upper()} THEN 2
                WHEN exchange_segment = 'NSE_EQ' AND instrument_type = 'EQUITY' THEN 0
                WHEN exchange_segment = 'BSE_EQ' AND instrument_type = 'EQUITY' THEN 1
                ELSE 2
            END,
            CASE
                WHEN CAST(instrument_token AS text) = {q_exact_ref} THEN 0
                WHEN CAST(security_id AS text) = {q_exact_ref} THEN 0
                WHEN {strike_exact_ref if strike_exact_ref else "NULL"} IS NOT NULL
                     AND {("CAST(strike_price AS text)" if has_strike_price else "''")} = {strike_exact_ref if strike_exact_ref else "''"} THEN 0
                WHEN upper(symbol) = upper({q_exact_ref}) THEN 0
                {(f"WHEN upper(COALESCE(trading_symbol, '')) = upper({q_exact_ref}) THEN 0" if has_trading_symbol else "")}
                WHEN upper(COALESCE(display_name, '')) = upper({q_exact_ref}) THEN 1
                {(f"WHEN upper(COALESCE(underlying, '')) = upper({q_exact_ref}) THEN 1" if has_underlying else "")}
                WHEN upper(symbol) LIKE upper({q_exact_ref}) || '%' THEN 2
                {(f"WHEN upper(COALESCE(trading_symbol, '')) LIKE upper({q_exact_ref}) || '%' THEN 2" if has_trading_symbol else "")}
                WHEN upper(COALESCE(display_name, '')) LIKE upper({q_exact_ref}) || '%' THEN 3
                {(f"WHEN upper(COALESCE(underlying, '')) LIKE upper({q_exact_ref}) || '%' THEN 3" if has_underlying else "")}
                ELSE 4
            END,
            length(symbol),
            symbol
        """
        if q and q_exact_ref
        else "ORDER BY exchange_segment, instrument_type, symbol"
    )
    
    sql = f"""
        SELECT 
            instrument_token, security_id, symbol, display_name,
            {("trading_symbol" if has_trading_symbol else "NULL::text AS trading_symbol")},
            {("underlying" if has_underlying else "NULL::text AS underlying")},
            {("strike_price" if has_strike_price else "NULL::numeric AS strike_price")},
            {("option_type" if has_option_type else "NULL::text AS option_type")},
            exchange_segment, instrument_type, tick_size, lot_size, tier
        FROM instrument_master
        WHERE {where_clause}
        {order_by}
        {limit_offset_clause}
    """
    
    rows = await pool.fetch(sql, *params)
    
    data = []
    for r in rows:
        tick_size = _to_float(r.get("tick_size"))
        token_str = str(r["instrument_token"])
        data.append(
            {
                "instrument_id": token_str,
                "instrument_token": token_str,  # ← Explicit for frontend chart adapter
                "security_id": token_str,       # ← Explicit for chart realtime WS subscription
                "display_symbol": r.get("display_name") or r.get("symbol"),
                "symbol": r.get("symbol"),
                "trading_symbol": r.get("trading_symbol"),
                "underlying": r.get("underlying"),
                "strike_price": _to_float(r.get("strike_price")),
                "option_type": r.get("option_type"),
                "exchange": r.get("exchange_segment"),
                "token": token_str,
                "tier": r.get("tier"),
                "price_precision": _precision_from_tick_size(r.get("tick_size")),
                "volume_precision": 0,
                "tick_size": tick_size,
                "lot_size": int(r.get("lot_size") or 1),
                "chart_type": "candles",
                "supported_intervals": sorted(SUPPORTED_INTERVALS),
            }
        )
    
    out: dict[str, Any] = {"ok": True, "data": data}
    if not data:
        out["meta"] = {"no_data": True, "reason": "NO_MATCHING_INSTRUMENTS"}
    else:
        out["meta"] = {"count": len(data), "offset": offset, "limit": limit}
    return out


# ── Seed Endpoint ──────────────────────────────────────────────────────────

_SEED_INTRADAY_INTERVALS = ["1", "5", "15", "25", "60"]  # native Dhan intraday intervals
_SEED_INTRADAY_YEARS = 5  # DhanHQ keeps 5 years of intraday data


@router.post("/chart/seed")
async def chart_seed(
    body: dict[str, Any],
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Admin-only: bulk-download all available historical candles for an instrument
    and persist them to the DB so the chart works offline / on refresh.

    Body (JSON):
        security_id       string | int   e.g. "1333"
        exchange_segment  string         e.g. "NSE_EQ"
        instrument_type   string         e.g. "EQUITY"   (default: EQUITY)
        intervals         list[str]      e.g. ["1","5","15","D"]  (default: all)

    Fetches:
        Daily   — from inception to today (one Dhan call)
        Intraday — last 5 years in 90-day windows
    """
    if current_user.role not in ("ADMIN", "SUPER_ADMIN"):
        return _error_response(403, "FORBIDDEN", "Admin access required.")

    if cfg.dhan_disabled:
        return _error_response(503, "DHAN_DISABLED", "DhanHQ connectivity is disabled.")

    raw_sid  = str(body.get("security_id", "")).strip()
    exch_seg = str(body.get("exchange_segment", "")).strip()
    inst     = str(body.get("instrument_type", "EQUITY")).strip().upper() or "EQUITY"

    if not raw_sid or not raw_sid.isdigit():
        return _error_response(400, "INVALID_REQUEST", "security_id (numeric string) is required.")
    if not exch_seg:
        return _error_response(400, "INVALID_REQUEST", "exchange_segment is required.")

    sec_id = int(raw_sid)

    requested = body.get("intervals") or (_SEED_INTRADAY_INTERVALS + ["D"])
    if not isinstance(requested, list):
        return _error_response(400, "INVALID_REQUEST", "`intervals` must be a list.")

    pool = get_pool()
    now_dt = datetime.now(tz=timezone.utc)
    results: dict[str, Any] = {}

    for iv in requested:
        iv = str(iv).strip()
        if iv == "D":
            # Daily — fetch from inception (DhanHQ holds all history)
            payload = {
                "securityId": raw_sid,
                "exchangeSegment": exch_seg,
                "instrument": inst,
                "oi": False,
                "fromDate": "2000-01-01",
                "toDate": now_dt.strftime("%Y-%m-%d"),
            }
            try:
                resp = await dhan_client.post("/charts/historical", json=payload)
            except Exception as exc:
                results[iv] = {"ok": False, "error": str(exc)}
                continue
            if resp.status_code != 200:
                results[iv] = {"ok": False, "http_status": resp.status_code, "body": resp.text[:300]}
                continue
            candles = _extract_candle_rows(resp.json())
            if candles:
                await _db_save_candles(pool, sec_id, exch_seg, "D", candles)
            results[iv] = {"ok": True, "candles_stored": len(candles)}

        elif iv in _SEED_INTRADAY_INTERVALS:
            # Map interval number to our internal key ("1" → "1m", etc.)
            # For seeding we always store under the Dhan native interval string
            # (same as UPSTREAM_INTERVAL values) but we need the internal key for DB.
            internal_iv = f"{iv}m" if iv != "60" else "60m"
            start_dt = now_dt - timedelta(days=_SEED_INTRADAY_YEARS * 365)
            all_candles: list[dict[str, Any]] = []
            window_start = start_dt
            fetch_ok = True
            while window_start <= now_dt:
                window_end = min(window_start + timedelta(days=90), now_dt)
                payload = {
                    "securityId": raw_sid,
                    "exchangeSegment": exch_seg,
                    "instrument": inst,
                    "interval": iv,
                    "oi": False,
                    "fromDate": window_start.strftime("%Y-%m-%d %H:%M:%S"),
                    "toDate": window_end.strftime("%Y-%m-%d %H:%M:%S"),
                }
                try:
                    resp = await dhan_client.post("/charts/intraday", json=payload)
                except Exception as exc:
                    results[internal_iv] = {"ok": False, "error": str(exc)}
                    fetch_ok = False
                    break
                if resp.status_code != 200:
                    results[internal_iv] = {"ok": False, "http_status": resp.status_code, "body": resp.text[:300]}
                    fetch_ok = False
                    break
                chunk = _extract_candle_rows(resp.json())
                all_candles.extend(chunk)
                log.info(
                    "[chart/seed] %s %s iv=%s window %s→%s: %d candles",
                    sec_id, exch_seg, iv,
                    window_start.strftime("%Y-%m-%d"),
                    window_end.strftime("%Y-%m-%d"),
                    len(chunk),
                )
                if window_end >= now_dt:
                    break
                window_start = window_end + timedelta(seconds=1)

            if fetch_ok:
                # Deduplicate before saving
                dedup = {int(c["timestamp"]): c for c in all_candles}
                candles = [dedup[k] for k in sorted(dedup.keys())]
                if candles:
                    await _db_save_candles(pool, sec_id, exch_seg, internal_iv, candles)
                results[internal_iv] = {"ok": True, "candles_stored": len(candles)}
        else:
            results[iv] = {"ok": False, "error": f"Unknown/unsupported interval for seeding: {iv}"}

    return {
        "ok": True,
        "security_id": sec_id,
        "exchange_segment": exch_seg,
        "results": results,
    }


@router.get("/chart/storage/settings")
async def chart_storage_settings(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT setting_key, setting_value
        FROM user_chart_settings
        WHERE user_id = $1::uuid
        """,
        user.id,
    )
    return {
        "settings": {str(r["setting_key"]): str(r["setting_value"]) for r in rows},
    }


@router.put("/chart/storage/settings/{setting_key}")
async def chart_storage_set_setting(
    setting_key: str,
    payload: dict[str, Any] = Body(default={}),
    user: CurrentUser = Depends(get_current_user),
):
    key = (setting_key or "").strip()
    if not key:
        return _error_response(400, "INVALID_REQUEST", "setting_key is required.")

    value = payload.get("value")
    if value is None:
        return _error_response(400, "INVALID_REQUEST", "value is required.")

    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO user_chart_settings (user_id, setting_key, setting_value)
        VALUES ($1::uuid, $2, $3)
        ON CONFLICT (user_id, setting_key)
        DO UPDATE SET setting_value = EXCLUDED.setting_value, updated_at = NOW()
        """,
        user.id,
        key,
        str(value),
    )
    return {"ok": True}


@router.delete("/chart/storage/settings/{setting_key}")
async def chart_storage_remove_setting(
    setting_key: str,
    user: CurrentUser = Depends(get_current_user),
):
    key = (setting_key or "").strip()
    if key:
        pool = get_pool()
        await pool.execute(
            "DELETE FROM user_chart_settings WHERE user_id = $1::uuid AND setting_key = $2",
            user.id,
            key,
        )
    return {"ok": True}


@router.get("/chart/storage/charts")
async def chart_storage_list_charts(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT id, name, COALESCE(symbol, '') AS symbol, COALESCE(resolution, '') AS resolution,
               EXTRACT(EPOCH FROM updated_at)::bigint AS updated_epoch
        FROM user_chart_layouts
        WHERE user_id = $1::uuid
        ORDER BY updated_at DESC
        """,
        user.id,
    )
    return {
        "charts": [
            {
                "id": int(r["id"]),
                "name": str(r["name"]),
                "symbol": str(r["symbol"]),
                "resolution": str(r["resolution"]),
                "timestamp": int(r["updated_epoch"] or 0),
            }
            for r in rows
        ]
    }


@router.post("/chart/storage/charts")
async def chart_storage_save_chart(
    payload: dict[str, Any] = Body(default={}),
    user: CurrentUser = Depends(get_current_user),
):
    name = str(payload.get("name") or "").strip()
    content = str(payload.get("content") or "")
    symbol = str(payload.get("symbol") or "").strip()
    resolution = str(payload.get("resolution") or "").strip()
    chart_id_raw = str(payload.get("id") or "").strip()

    if not name:
        return _error_response(400, "INVALID_REQUEST", "name is required.")
    if not content:
        return _error_response(400, "INVALID_REQUEST", "content is required.")

    pool = get_pool()
    if chart_id_raw.isdigit():
        row = await pool.fetchrow(
            """
            UPDATE user_chart_layouts
            SET name = $3, symbol = $4, resolution = $5, content = $6, updated_at = NOW()
            WHERE id = $1::bigint AND user_id = $2::uuid
            RETURNING id
            """,
            int(chart_id_raw),
            user.id,
            name,
            symbol,
            resolution,
            content,
        )
        if row:
            return {"id": str(row["id"])}

    row = await pool.fetchrow(
        """
        INSERT INTO user_chart_layouts (user_id, name, symbol, resolution, content)
        VALUES ($1::uuid, $2, $3, $4, $5)
        RETURNING id
        """,
        user.id,
        name,
        symbol,
        resolution,
        content,
    )
    return {"id": str(row["id"])}


@router.get("/chart/storage/charts/{chart_id}")
async def chart_storage_get_chart_content(
    chart_id: int,
    user: CurrentUser = Depends(get_current_user),
):
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT content
        FROM user_chart_layouts
        WHERE id = $1::bigint AND user_id = $2::uuid
        """,
        chart_id,
        user.id,
    )
    if not row:
        return _error_response(404, "NOT_FOUND", "Chart not found.")
    return {"content": str(row["content"])}


@router.delete("/chart/storage/charts/{chart_id}")
async def chart_storage_remove_chart(
    chart_id: int,
    user: CurrentUser = Depends(get_current_user),
):
    pool = get_pool()
    await pool.execute(
        "DELETE FROM user_chart_layouts WHERE id = $1::bigint AND user_id = $2::uuid",
        chart_id,
        user.id,
    )
    return {"ok": True}


@router.get("/chart/storage/study-templates")
async def chart_storage_list_study_templates(user: CurrentUser = Depends(get_current_user)):
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT name
        FROM user_chart_study_templates
        WHERE user_id = $1::uuid
        ORDER BY updated_at DESC
        """,
        user.id,
    )
    return {"templates": [{"name": str(r["name"])} for r in rows]}


@router.post("/chart/storage/study-templates")
async def chart_storage_save_study_template(
    payload: dict[str, Any] = Body(default={}),
    user: CurrentUser = Depends(get_current_user),
):
    name = str(payload.get("name") or "").strip()
    content = str(payload.get("content") or "")
    if not name:
        return _error_response(400, "INVALID_REQUEST", "name is required.")
    if not content:
        return _error_response(400, "INVALID_REQUEST", "content is required.")

    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO user_chart_study_templates (user_id, name, content)
        VALUES ($1::uuid, $2, $3)
        ON CONFLICT (user_id, name)
        DO UPDATE SET content = EXCLUDED.content, updated_at = NOW()
        """,
        user.id,
        name,
        content,
    )
    return {"ok": True}


@router.get("/chart/storage/study-templates/{template_name}")
async def chart_storage_get_study_template(
    template_name: str,
    user: CurrentUser = Depends(get_current_user),
):
    name = (template_name or "").strip()
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT content
        FROM user_chart_study_templates
        WHERE user_id = $1::uuid AND name = $2
        """,
        user.id,
        name,
    )
    if not row:
        return _error_response(404, "NOT_FOUND", "Study template not found.")
    return {"content": str(row["content"])}


@router.delete("/chart/storage/study-templates/{template_name}")
async def chart_storage_remove_study_template(
    template_name: str,
    user: CurrentUser = Depends(get_current_user),
):
    name = (template_name or "").strip()
    if name:
        pool = get_pool()
        await pool.execute(
            "DELETE FROM user_chart_study_templates WHERE user_id = $1::uuid AND name = $2",
            user.id,
            name,
        )
    return {"ok": True}


@router.get("/chart/storage/drawing-templates/{tool_name}")
async def chart_storage_list_drawing_templates(
    tool_name: str,
    user: CurrentUser = Depends(get_current_user),
):
    tool = (tool_name or "").strip()
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT template_name
        FROM user_chart_drawing_templates
        WHERE user_id = $1::uuid AND tool_name = $2
        ORDER BY template_name
        """,
        user.id,
        tool,
    )
    return {"templates": [str(r["template_name"]) for r in rows]}


@router.get("/chart/storage/drawing-templates/{tool_name}/{template_name}")
async def chart_storage_get_drawing_template(
    tool_name: str,
    template_name: str,
    user: CurrentUser = Depends(get_current_user),
):
    tool = (tool_name or "").strip()
    name = (template_name or "").strip()
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT content
        FROM user_chart_drawing_templates
        WHERE user_id = $1::uuid AND tool_name = $2 AND template_name = $3
        """,
        user.id,
        tool,
        name,
    )
    if not row:
        return _error_response(404, "NOT_FOUND", "Drawing template not found.")
    return {"content": str(row["content"])}


@router.post("/chart/storage/drawing-templates")
async def chart_storage_save_drawing_template(
    payload: dict[str, Any] = Body(default={}),
    user: CurrentUser = Depends(get_current_user),
):
    tool = str(payload.get("tool_name") or "").strip()
    name = str(payload.get("template_name") or "").strip()
    content = str(payload.get("content") or "")
    if not tool or not name:
        return _error_response(400, "INVALID_REQUEST", "tool_name and template_name are required.")
    if not content:
        return _error_response(400, "INVALID_REQUEST", "content is required.")

    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO user_chart_drawing_templates (user_id, tool_name, template_name, content)
        VALUES ($1::uuid, $2, $3, $4)
        ON CONFLICT (user_id, tool_name, template_name)
        DO UPDATE SET content = EXCLUDED.content, updated_at = NOW()
        """,
        user.id,
        tool,
        name,
        content,
    )
    return {"ok": True}


@router.delete("/chart/storage/drawing-templates/{tool_name}/{template_name}")
async def chart_storage_remove_drawing_template(
    tool_name: str,
    template_name: str,
    user: CurrentUser = Depends(get_current_user),
):
    tool = (tool_name or "").strip()
    name = (template_name or "").strip()
    if tool and name:
        pool = get_pool()
        await pool.execute(
            """
            DELETE FROM user_chart_drawing_templates
            WHERE user_id = $1::uuid AND tool_name = $2 AND template_name = $3
            """,
            user.id,
            tool,
            name,
        )
    return {"ok": True}


async def _resolve_ws_user(websocket: WebSocket) -> CurrentUser | None:
    token = None
    x_auth = websocket.headers.get("x-auth")
    auth = websocket.headers.get("authorization")

    if x_auth and x_auth.strip():
        token = x_auth.strip()
    elif auth and auth.strip():
        token = auth.replace("Bearer ", "").strip()
    else:
        qp = websocket.query_params
        token = (qp.get("token") or qp.get("auth") or "").strip() or None

    if not token:
        return None

    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT u.id, u.name, u.mobile, u.role, u.admin_tab_permissions
        FROM user_sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token = $1::uuid
          AND s.expires_at > NOW()
          AND u.is_active = TRUE
        """,
        token,
    )
    if not row:
        return None

    return CurrentUser(
        id=str(row["id"]),
        name=row["name"],
        mobile=row["mobile"],
        role=row["role"],
        permissions=list(row["admin_tab_permissions"] or []),
    )


async def _send_ws(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(payload))


@router.websocket("/ws/chart")
async def chart_ws(websocket: WebSocket):
    user = await _resolve_ws_user(websocket)
    await websocket.accept()

    if not user:
        await _send_ws(
            websocket,
            {
                "type": "error",
                "code": "UNAUTHORIZED",
                "message": "Authentication required.",
                "retryable": False,
                "timestamp": int(datetime.now(tz=timezone.utc).timestamp() * 1000),
            },
        )
        await websocket.close(code=4401)
        return

    subscriptions: set[int] = set()
    last_sent_ms: dict[int, int] = {}
    last_heartbeat_ms = 0

    while True:
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            msg = json.loads(raw)
            action = (msg.get("action") or "").strip().lower()

            if action == "ping":
                await _send_ws(
                    websocket,
                    {
                        "type": "heartbeat",
                        "server_timestamp": now_ms,
                        "reply_to": msg.get("ts"),
                    },
                )
                continue

            if action in {"subscribe", "unsubscribe"}:
                instruments = msg.get("instruments") or []
                if not isinstance(instruments, list):
                    await _send_ws(
                        websocket,
                        {
                            "type": "error",
                            "code": "INVALID_REQUEST",
                            "message": "`instruments` must be a list.",
                            "retryable": False,
                            "timestamp": now_ms,
                        },
                    )
                    continue

                accepted: list[str] = []
                rejected: list[dict[str, Any]] = []
                resolved_tokens: list[int] = []

                for item in instruments:
                    if not isinstance(item, dict):
                        rejected.append(
                            {
                                "key": str(item),
                                "code": "INVALID_REQUEST",
                                "message": "Instrument item must be an object.",
                            }
                        )
                        continue

                    resolved = await _resolve_instrument(
                        instrument_id=item.get("instrument_id"),
                        symbol=item.get("symbol"),
                    )
                    if not resolved:
                        rejected.append(
                            {
                                "key": str(item.get("instrument_id") or item.get("symbol") or ""),
                                "code": "INVALID_SYMBOL",
                                "message": "Instrument not found.",
                            }
                        )
                        continue

                    token = int(resolved["instrument_token"])
                    resolved_tokens.append(token)
                    accepted.append(str(token))

                if action == "subscribe":
                    for t in resolved_tokens:
                        subscriptions.add(t)
                else:
                    for t in resolved_tokens:
                        subscriptions.discard(t)

                await _send_ws(
                    websocket,
                    {
                        "type": "subscription_ack",
                        "request_id": msg.get("request_id"),
                        "accepted": accepted,
                        "rejected": rejected,
                        "timestamp": now_ms,
                    },
                )

                if action == "subscribe" and accepted:
                    pool = get_pool()
                    snap_rows = await pool.fetch(
                        """
                        SELECT im.instrument_token,
                               im.symbol,
                               im.display_name,
                               im.exchange_segment,
                               md.updated_at,
                               md.ltt,
                               md.ltp,
                               md.open,
                               md.high,
                               md.low,
                               md.close
                        FROM instrument_master im
                        LEFT JOIN market_data md ON md.instrument_token = im.instrument_token
                        WHERE im.instrument_token = ANY($1::bigint[])
                        """,
                        [int(x) for x in accepted],
                    )

                    for r in snap_rows:
                        ts = _to_epoch_ms(r.get("ltt")) or _to_epoch_ms(r.get("updated_at")) or now_ms
                        last_sent_ms[int(r["instrument_token"])] = ts
                        await _send_ws(
                            websocket,
                            {
                                "type": "initial_snapshot",
                                "instrument_id": str(r["instrument_token"]),
                                "timestamp": ts,
                                "last_price": _to_float(r.get("ltp")),
                                "open": _to_float(r.get("open")),
                                "high": _to_float(r.get("high")),
                                "low": _to_float(r.get("low")),
                                "close": _to_float(r.get("close")),
                                "previous_close": _to_float(r.get("close")),
                                "volume": None,
                            },
                        )

                continue

            await _send_ws(
                websocket,
                {
                    "type": "error",
                    "code": "INVALID_REQUEST",
                    "message": "Unknown action.",
                    "retryable": False,
                    "timestamp": now_ms,
                },
            )

        except asyncio.TimeoutError:
            pass
        except WebSocketDisconnect:
            break
        except Exception:
            pass

        if subscriptions:
            try:
                pool = get_pool()
                rows = await pool.fetch(
                    """
                    SELECT instrument_token, ltt, updated_at, ltp
                    FROM market_data
                    WHERE instrument_token = ANY($1::bigint[])
                    """,
                    list(subscriptions),
                )
                for r in rows:
                    token = int(r["instrument_token"])
                    event_ms = _to_epoch_ms(r.get("ltt")) or _to_epoch_ms(r.get("updated_at"))
                    if event_ms is None:
                        continue

                    if event_ms <= int(last_sent_ms.get(token, 0)):
                        continue

                    await _send_ws(
                        websocket,
                        {
                            "type": "live_update",
                            "instrument_id": str(token),
                            "event_timestamp": event_ms,
                            "traded_price": _to_float(r.get("ltp")),
                            "volume": None,
                            "volume_delta": None,
                        },
                    )
                    last_sent_ms[token] = event_ms
            except Exception as exc:
                log.warning("/ws/chart live update poll failed: %s", exc)

        if now_ms - last_heartbeat_ms >= 20_000:
            await _send_ws(
                websocket,
                {
                    "type": "heartbeat",
                    "server_timestamp": now_ms,
                    "reply_to": None,
                },
            )
            last_heartbeat_ms = now_ms
