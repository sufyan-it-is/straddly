"""
app/market_data/static_auth_monitor.py
======================================
Background task monitoring static IP auth health.

Detects failures during runtime and triggers fallback to auto_totp.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.credentials.credential_store import (
    get_active_auth_mode,
    set_auth_mode,
    is_static_configured,
)
from app.runtime.notifications import add_notification

log = logging.getLogger(__name__)


class StaticAuthMonitor:
    """
    Watches for auth failures during runtime.
    
    Failure triggers: 401, 403, signature rejection, repeated timeouts
    On failure: switch auth_mode → auto_totp, log reason, notify admin endpoint
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._failure_count: int = 0
        self._failure_threshold: int = 3
        self._failure_reset_minutes: int = 60
        self._last_failure_time: Optional[datetime] = None
        self._monitoring_enabled: bool = False

    async def start(self) -> None:
        """Start background monitoring task."""
        if not is_static_configured():
            log.debug("[StaticAuthMonitor] Static credentials not configured — skipping.")
            self._monitoring_enabled = False
            return

        self._monitoring_enabled = True
        self._task = asyncio.create_task(self._monitor_loop(), name="static_auth_monitor")
        log.info("[StaticAuthMonitor] Started.")

    async def stop(self) -> None:
        """Graceful shutdown."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("[StaticAuthMonitor] Stopped.")

    async def record_failure(
        self,
        status_code: Optional[int] = None,
        reason: str = "unknown",
    ) -> None:
        """Record a static auth failure and check if threshold reached."""
        if not self._monitoring_enabled:
            return

        now = datetime.now(tz=timezone.utc)
        
        # Reset counter if last failure was > reset window ago
        if self._last_failure_time:
            elapsed = now - self._last_failure_time
            if elapsed > timedelta(minutes=self._failure_reset_minutes):
                self._failure_count = 0
                log.debug(
                    "[StaticAuthMonitor] Failure counter reset (no failures in %dmin)",
                    self._failure_reset_minutes,
                )

        self._failure_count += 1
        self._last_failure_time = now

        reason_detail = f"HTTP {status_code}: {reason}" if status_code else reason
        log.warning(
            "[StaticAuthMonitor] Static IP auth failure (%s) — count: %d/%d",
            reason_detail,
            self._failure_count,
            self._failure_threshold,
        )

        if self._failure_count >= self._failure_threshold:
            await self._trigger_fallback(reason_detail)

    async def _trigger_fallback(self, reason: str) -> None:
        """Switch mode to auto_totp and notify admin."""
        log.error(
            "[StaticAuthMonitor] Threshold reached. Switching auth_mode → auto_totp. "
            "Reason: %s",
            reason,
        )
        
        try:
            await set_auth_mode("auto_totp")
            self._failure_count = 0
            self._last_failure_time = None
        except Exception as exc:
            log.error("[StaticAuthMonitor] Failed to switch mode: %s", exc)

    def get_failure_count(self) -> int:
        """Return current failure count for admin diagnostics."""
        return self._failure_count

    def get_last_failure_time(self) -> Optional[datetime]:
        """Return timestamp of last recorded failure."""
        return self._last_failure_time

    async def reset_failures(self) -> None:
        """
        Reset failure counter (used when operator retries after fixing underlying issue).
        Call this when manually reattempting static IP auth.
        """
        prev_count = self._failure_count
        self._failure_count = 0
        self._last_failure_time = None
        log.info(
            "[StaticAuthMonitor] Failure counter reset by operator. "
            "Previous count: %d/3 — Ready for manual reattempt.",
            prev_count,
        )

    async def _monitor_loop(self) -> None:
        """
        Background loop for periodic health checks (optional).
        Currently just runs in the background; failures are recorded
        as they occur during normal REST calls.
        """
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            
            if not is_static_configured() or get_active_auth_mode() != "static_ip":
                log.debug("[StaticAuthMonitor] Static auth not active — skipping check.")
                continue

            # Log current failure state for observability
            log.debug(
                "[StaticAuthMonitor] Health check — failure_count=%d/%d, "
                "monitoring=%s",
                self._failure_count,
                self._failure_threshold,
                self._monitoring_enabled,
            )


# Singleton
static_auth_monitor = StaticAuthMonitor()


# Notification helper for admin endpoint
async def log_auth_failure_event(
    status_code: Optional[int],
    reason: str,
) -> None:
    """
    Log auth incident to system_notifications table.
    Called when a critical failure is triggered.
    """
    try:
        await add_notification(
            category="authentication",
            severity="critical",
            title="Static IP Auth Fallback Triggered",
            message=(
                f"Static IP authentication failed (HTTP {status_code}): {reason}. "
                "Switched to auto_totp mode."
            ),
            dedupe_key="static-auth-fallback",
            dedupe_ttl_seconds=600,
        )
    except Exception as exc:
        log.warning("[StaticAuthMonitor] Could not log notification: %s", exc)
