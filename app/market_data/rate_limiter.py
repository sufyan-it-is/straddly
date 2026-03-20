"""
app/market_data/rate_limiter.py
================================
Two-layer rate limiting for all DhanHQ REST calls:

  Layer 1 — SlidingWindowRateLimiter
    Per-endpoint sliding-window limiters.  Callers can still use these
    directly, but should prefer DhanHttpClient (Layer 2).

  Layer 2 — DhanHttpClient  (universal gateway)
    The single httpx wrapper that EVERY outbound DhanHQ REST call must go
    through.  It:
      • Resolves the correct per-endpoint limiter from the URL path
      • Awaits it before sending (so no call ever escapes rate control)
      • Tracks counters: total calls, per-endpoint calls, throttle delays,
        and HTTP-error counts
      • Logs a WARNING when a call had to be delayed (throttle event)
    Use `dhan_client.post(...)` / `dhan_client.get(...)` everywhere instead
    of creating raw httpx.AsyncClient instances.

Rate limits (DhanHQ v2 DATA APIs):
  /optionchain          → 1 req / 3 sec
  /marketfeed/ltp|…     → 1 req / sec
  everything else       → 1 req / sec  (conservative fallback)
"""
import asyncio
import hashlib
import time
import logging
from collections import deque
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Layer 1 — SlidingWindowRateLimiter
# ══════════════════════════════════════════════════════════════════════════

class SlidingWindowRateLimiter:
    """
    Allows at most `max_calls` within the last `window_seconds`.
    Async-safe: awaits (sleeps) until the window admits the next call.
    """

    def __init__(self, max_calls: int, window_seconds: float, name: str = ""):
        self._max_calls = max_calls
        self._window    = window_seconds
        self._name      = name
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """
        Blocks until a slot is free.  Returns the wait time in seconds
        (0.0 if no wait was needed) so callers can log/count throttle events.
        """
        async with self._lock:
            now = time.monotonic()
            while self._timestamps and self._timestamps[0] < now - self._window:
                self._timestamps.popleft()

            waited = 0.0
            if len(self._timestamps) >= self._max_calls:
                sleep_for = self._window - (now - self._timestamps[0]) + 0.001
                if sleep_for > 0:
                    log.debug(
                        f"[RateLimiter:{self._name}] throttling {sleep_for:.3f}s"
                    )
                    await asyncio.sleep(sleep_for)
                    waited = sleep_for

            self._timestamps.append(time.monotonic())
            return waited

    def recent_call_count(self) -> int:
        """Number of calls recorded within the current window."""
        now = time.monotonic()
        return sum(1 for t in self._timestamps if t >= now - self._window)


class MultiWindowRateLimiter:
    """
    Enforces multiple rolling-window limits at once.

    Used to keep all outbound Dhan REST traffic within documented global
    budgets (per-second, per-minute, per-hour, per-day).
    """

    def __init__(self, windows: list[tuple[int, float]], name: str = ""):
        self._name = name
        self._windows = windows
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        async with self._lock:
            now = time.monotonic()
            max_window = max(w for _, w in self._windows)
            while self._timestamps and self._timestamps[0] < now - max_window:
                self._timestamps.popleft()

            sleep_for = 0.0
            for limit, window in self._windows:
                recent = 0
                for ts in reversed(self._timestamps):
                    if ts < now - window:
                        break
                    recent += 1
                if recent >= limit:
                    earliest_in_window = None
                    for ts in self._timestamps:
                        if ts >= now - window:
                            earliest_in_window = ts
                            break
                    if earliest_in_window is not None:
                        sleep_for = max(sleep_for, window - (now - earliest_in_window) + 0.001)

            waited = 0.0
            if sleep_for > 0:
                log.debug(f"[RateLimiter:{self._name}] throttling {sleep_for:.3f}s (multi-window)")
                await asyncio.sleep(sleep_for)
                waited = sleep_for

            self._timestamps.append(time.monotonic())
            return waited


# ── Per-endpoint singleton limiters ────────────────────────────────────────

# POST /marketfeed/ltp|ohlc|quote  → 1 req/sec
market_quote_limiter = SlidingWindowRateLimiter(
    max_calls=1, window_seconds=1.0, name="market_quote"
)

# POST /optionchain  → conservative: 1 req / 4 sec
# (Dhan docs often state 1/3s, but in practice accounts can still get 429 when
# running near the boundary, especially alongside other traffic.)
option_chain_limiter = SlidingWindowRateLimiter(
    max_calls=1, window_seconds=4.0, name="option_chain"
)

# Conservative fallback for any other endpoint
_default_limiter = SlidingWindowRateLimiter(
    max_calls=1, window_seconds=1.0, name="default"
)

# Global DhanHQ REST budget from official v2 docs (conservative hard cap).
# Applied in addition to endpoint-specific limiters.
_global_rest_budget = MultiWindowRateLimiter(
    windows=[
        (10, 1.0),            # per second
        (250, 60.0),          # per minute
        (1000, 60.0 * 60.0),  # per hour
        (7000, 24.0 * 60.0 * 60.0),  # per day
    ],
    name="global_rest_budget",
)


def _resolve_limiter(path: str) -> SlidingWindowRateLimiter:
    """Map a URL path to its designated rate limiter."""
    p = path.lower()
    if "optionchain" in p:
        return option_chain_limiter
    if "marketfeed" in p or "market-quote" in p:
        return market_quote_limiter
    return _default_limiter


# ══════════════════════════════════════════════════════════════════════════
# Layer 2 — DhanHttpClient  (universal gateway)
# ══════════════════════════════════════════════════════════════════════════

class _CallStats:
    def __init__(self):
        self.total_calls:      int   = 0
        self.throttle_events:  int   = 0
        self.total_wait_sec:   float = 0.0
        self.error_counts:     dict[int, int] = {}   # HTTP status → count
        self.per_endpoint:     dict[str, int] = {}   # path prefix → count

    def record(self, path: str, waited: float, status_code: int) -> None:
        self.total_calls += 1
        if waited > 0:
            self.throttle_events += 1
            self.total_wait_sec  += waited
        if status_code >= 400:
            self.error_counts[status_code] = self.error_counts.get(status_code, 0) + 1
        prefix = path.strip("/").split("/")[0]
        self.per_endpoint[prefix] = self.per_endpoint.get(prefix, 0) + 1

    def to_dict(self) -> dict:
        return {
            "total_calls":     self.total_calls,
            "throttle_events": self.throttle_events,
            "total_wait_sec":  round(self.total_wait_sec, 3),
            "error_counts":    self.error_counts,
            "per_endpoint":    self.per_endpoint,
            "current_window_calls": {
                "market_quote": market_quote_limiter.recent_call_count(),
                "option_chain": option_chain_limiter.recent_call_count(),
            },
        }


class DhanHttpClient:
    """
    Universal DhanHQ REST gateway.

    Usage:
        resp = await dhan_client.post("/optionchain", json={...})
        resp = await dhan_client.get("/some-endpoint")

    Every call automatically:
      1. Selects the correct SlidingWindowRateLimiter for the path
      2. Awaits it (blocks if needed, never exceeds DhanHQ limits)
      3. Injects auth headers from credential_store
      4. Records stats (counters, throttle events, HTTP errors)
    """

    def __init__(self):
        self._stats = _CallStats()
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        cfg = get_settings()
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=cfg.dhan_base_url,
                timeout=10.0,
            )
        return self._client

    async def _call(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        from app.credentials.credential_store import (
            get_active_auth_mode,
            get_rest_client_id,
            get_rest_headers,
            is_static_configured,
            serialize_static_body,
        )
        limiter = _resolve_limiter(path)
        waited_global = await _global_rest_budget.acquire()
        waited_local = await limiter.acquire()
        waited = waited_global + waited_local
        if waited > 0:
            log.warning(
                f"[DhanClient] Throttled {path!r} — waited {waited:.3f}s "
                f"(total throttle events: {self._stats.throttle_events + 1})"
            )
        body_payload: str | None = None
        # Inject dhanClientId into every POST/PUT body (DhanHQ requirement)
        if method in ("POST", "PUT") and "json" in kwargs:
            body = dict(kwargs["json"])
            body.setdefault("dhanClientId", get_rest_client_id())
            if get_active_auth_mode() == "static_ip" and is_static_configured():
                body_payload = serialize_static_body(body)
                kwargs.pop("json", None)
                kwargs["content"] = body_payload.encode("utf-8")
            else:
                kwargs["json"] = body
        body = kwargs.get("json") if method in ("POST", "PUT") else None
        # Merge caller-supplied headers with auth headers
        headers = {
            **get_rest_headers(method=method, path=path, body=body, body_payload=body_payload),
            **kwargs.pop("headers", {}),
        }
        if method in ("POST", "PUT") and get_active_auth_mode() == "static_ip" and is_static_configured():
            body_client_id = (body or {}).get("dhanClientId") if isinstance(body, dict) else None
            header_client_id = headers.get("client-id")
            if not body_payload:
                log.error("[DhanClient] static_ip signing mismatch for %s: serialized body payload missing", path)
            elif body_client_id != header_client_id:
                log.error(
                    "[DhanClient] static_ip signing mismatch for %s: body dhanClientId=%r header client-id=%r",
                    path,
                    body_client_id,
                    header_client_id,
                )
            else:
                log.debug(
                    "[DhanClient] static_ip POST signing ready for %s: client-id parity ok, payload_sha256=%s",
                    path,
                    hashlib.sha256(body_payload.encode("utf-8")).hexdigest()[:12],
                )
        resp = await self._get_client().request(
            method, path, headers=headers, **kwargs
        )
        self._stats.record(path, waited, resp.status_code)
        if resp.status_code >= 400:
            log.error(
                f"[DhanClient] {method} {path} → HTTP {resp.status_code}: "
                f"{resp.text[:200]}"
            )
            # Record static auth failures for fallback trigger
            if resp.status_code in (401, 403):
                try:
                    from app.market_data.static_auth_monitor import static_auth_monitor
                    await static_auth_monitor.record_failure(
                        status_code=resp.status_code,
                        reason="authentication failed",
                    )
                except Exception as e:
                    log.warning(f"[DhanClient] Failed to record auth failure: {e}")
            elif resp.status_code == 429:
                try:
                    from app.market_data.static_auth_monitor import static_auth_monitor
                    await static_auth_monitor.record_failure(
                        status_code=429,
                        reason="rate limit exceeded (possible signature rejection)",
                    )
                except Exception as e:
                    log.warning(f"[DhanClient] Failed to record rate limit failure: {e}")
        return resp

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._call("POST", path, **kwargs)

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._call("GET", path, **kwargs)

    async def verify_static_auth(self) -> httpx.Response:
        """
        Run a lightweight auth verification call via Dhan API.
        Caller must interpret status codes for pass/fail.
        """
        return await self._call("GET", "/profile")

    def get_stats(self) -> dict:
        """Live rate-limit metrics — served by GET /admin/rate-limits."""
        return self._stats.to_dict()

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# Singleton — import this everywhere instead of creating httpx.AsyncClient directly
dhan_client = DhanHttpClient()

