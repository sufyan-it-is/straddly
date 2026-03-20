"""
app/credentials/token_refresher.py
=====================================
Automatic DhanHQ token refresh using TOTP + PIN.

How it works
────────────
1. On startup the service generates a brand-new token via DhanHQ's
   /app/generateAccessToken endpoint (so the stored token is always fresh).
2. A background loop wakes every REFRESH_CHECK_MINUTES and regenerates the
   token whenever less than REFRESH_AHEAD_MINUTES are left before expiry.
3. All WebSockets are reconnected automatically after each refresh (handled
   inside credential_store.rotate_token).

Prerequisites (set in .env):
  DHAN_CLIENT_ID      — your DhanHQ client ID
  DHAN_PIN            — your 6-digit Dhan login PIN
  DHAN_TOTP_SECRET    — the TOTP shared secret from DhanHQ web
                        (shown as a plain string when you set up TOTP)

How to get DHAN_TOTP_SECRET
────────────────────────────
1. Log in to web.dhan.co → DhanHQ Trading APIs → Setup TOTP
2. After confirming with OTP, Dhan shows you both a QR code AND a plain
   text secret string (e.g. "JBSWY3DPEHPK3PXP").
3. Copy that string — it is your DHAN_TOTP_SECRET.
4. Also scan the QR in Google Authenticator / Authy for manual backup.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pyotp

log = logging.getLogger(__name__)

# ── Tuning knobs ─────────────────────────────────────────────────────────────
GENERATE_TOKEN_URL     = "https://auth.dhan.co/app/generateAccessToken"
REFRESH_CHECK_MINUTES  = 30    # how often the background loop wakes up
REFRESH_AHEAD_MINUTES  = 120   # refresh when this many minutes remain before expiry
RETRY_DELAY_SECONDS    = 60    # wait before retrying after a failure
MAX_RETRIES            = 5     # max consecutive failures before giving up


class TokenRefresher:
    """
    Background service: keeps the DhanHQ access token perpetually valid
    by auto-regenerating it with TOTP before it expires.

    Two modes (switchable live via Admin dashboard):
      auto_totp  — DEFAULT: generates/renews token automatically using TOTP+PIN
      manual     — FALLBACK: auto-refresh paused; admin pastes token by hand
    """

    def __init__(self) -> None:
        self._task:       asyncio.Task | None = None
        self._client_id:  str = ""
        self._pin:        str = ""
        self._totp_obj:   pyotp.TOTP | None = None
        self._enabled:    bool = False   # True when TOTP credentials are present
        self._paused:     bool = False   # True when admin has switched to manual mode

    def _load_runtime_credentials(self) -> None:
        """Load client/TOTP credentials from runtime store with env fallback already applied."""
        from app.credentials.credential_store import get_client_id, get_totp_credentials

        self._client_id = (get_client_id() or "").strip()
        totp_cfg = get_totp_credentials(masked=False)
        self._pin = (totp_cfg.get("pin") or "").strip()
        totp_secret = (totp_cfg.get("totp_secret") or "").strip().upper().replace(" ", "")

        if self._client_id and self._pin and totp_secret:
            self._totp_obj = pyotp.TOTP(totp_secret)
            self._enabled = True
        else:
            self._totp_obj = None
            self._enabled = False

    # ── Public API ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        """
        Called once at application startup.
        Loads TOTP credentials from config, loads stored auth_mode from DB,
        generates a fresh token immediately (if mode is auto_totp),
        then spawns the background refresh loop.
        """
        if self._task and not self._task.done():
            return

        from app.credentials.credential_store import get_auth_mode
        self._load_runtime_credentials()

        if not self._enabled:
            log.warning(
                "[TokenRefresher] DHAN_PIN or DHAN_TOTP_SECRET not configured (env/DB) — "
                "auto-refresh DISABLED.  You must rotate the token manually "
                "via POST /admin/credentials/rotate every 24 hours."
            )
            return

        # Respect the persisted auth_mode from DB
        stored_mode = get_auth_mode()   # already loaded by load_credentials()
        if stored_mode == "manual":
            self._paused = True
            log.info(
                "[TokenRefresher] Auth mode is 'manual' (persisted in DB) — "
                "auto-refresh is PAUSED.  Use Admin dashboard to switch back."
            )
            # Still spawn the loop so it's ready when un-paused
            self._task = asyncio.create_task(self._loop(), name="token_refresh_loop")
            return

        # auto_totp mode: generate a fresh token immediately
        log.info("[TokenRefresher] TOTP mode active — generating initial token…")
        success = await self._do_refresh(reason="startup")
        if not success:
            log.warning(
                "[TokenRefresher] Could not obtain an initial DhanHQ token. "
                "Market data feeds will be unavailable until valid credentials "
                "are provided via the Admin dashboard. "
                "Check DHAN_PIN / DHAN_TOTP_SECRET in .env."
            )
            # Still spawn the loop so it retries every hour
            self._task = asyncio.create_task(self._loop(), name="token_refresh_loop")
            return

        # Spawn background watcher
        self._task = asyncio.create_task(self._loop(), name="token_refresh_loop")
        log.info("[TokenRefresher] Background refresh loop started.")

    async def stop(self) -> None:
        """Graceful shutdown — cancel the background task."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("[TokenRefresher] Stopped.")

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def pause(self) -> None:
        """
        Switch to MANUAL mode: stop auto-refresh.
        The admin is responsible for pasting a token via
        POST /admin/credentials/rotate.
        The background loop task keeps running but skips refreshes.
        """
        self._paused = True
        log.info(
            "[TokenRefresher] Switched to MANUAL auth mode. "
            "Auto token refresh is PAUSED."
        )

    async def resume(self) -> None:
        """
        Switch back to AUTO_TOTP mode: restart auto-refresh.
        Immediately generates a fresh token so the switch takes effect now.
        Raises if TOTP credentials are not configured.
        """
        self._load_runtime_credentials()
        if not self._enabled:
            raise RuntimeError(
                "Cannot resume auto-refresh: TOTP credentials "
                "(DHAN_PIN / DHAN_TOTP_SECRET) are not configured."
            )
        self._paused = False
        log.info(
            "[TokenRefresher] Switched to AUTO_TOTP mode. "
            "Generating a fresh token now…"
        )
        success = await self._do_refresh(reason="resume")
        if not success:
            # Rollback paused state so admin can try again
            self._paused = True
            raise RuntimeError(
                "auto_totp resume failed: token generation unsuccessful. "
                "Still in manual mode. Check logs and try again."
            )
        log.info("[TokenRefresher] Auto-refresh resumed successfully.")

    async def refresh_now(self) -> dict:
        """
        Force an immediate token refresh (admin-triggered).
        Works even when paused — useful as an emergency re-gen in manual mode.
        Returns a status dict.
        """
        self._load_runtime_credentials()
        if not self._enabled:
            return {"success": False, "reason": "TOTP not configured"}
        success = await self._do_refresh(reason="manual_admin")
        if success:
            from app.credentials.credential_store import get_token_expiry
            expiry = get_token_expiry()
            return {
                "success": True,
                "new_expiry": expiry.isoformat() if expiry else None,
            }
        return {"success": False, "reason": "API call failed — see logs"}

    @property
    def is_enabled(self) -> bool:
        """True when TOTP credentials are present (regardless of pause state)."""
        return self._enabled

    @property
    def is_paused(self) -> bool:
        """True when the admin has put the refresher in manual mode."""
        return self._paused

    @property
    def effective_mode(self) -> str:
        """Human-readable active mode string."""
        if not self._enabled:
            return "disabled"   # TOTP creds not set at all
        if self._paused:
            return "manual"     # Admin has paused TOTP
        return "auto_totp"      # Running normally

    # ── Internal ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Background loop: wakes every REFRESH_CHECK_MINUTES."""
        while True:
            await asyncio.sleep(REFRESH_CHECK_MINUTES * 60)
            if not self._enabled:
                break
            if self._paused:
                log.debug("[TokenRefresher] Auto-refresh is paused (manual mode) — skipping.")
                continue
            try:
                if self._should_refresh():
                    log.info("[TokenRefresher] Token nearing expiry — refreshing…")
                    await self._do_refresh(reason="scheduled")
                else:
                    log.debug("[TokenRefresher] Token still valid — no action needed.")
            except Exception as exc:  # noqa: BLE001
                log.exception(f"[TokenRefresher] Unexpected error in loop: {exc}")

    def _should_refresh(self) -> bool:
        """True when the current token expires within REFRESH_AHEAD_MINUTES."""
        from app.credentials.credential_store import get_token_expiry
        expiry = get_token_expiry()
        if expiry is None:
            return True  # No expiry recorded → definitely refresh
        threshold = datetime.now(tz=timezone.utc) + timedelta(minutes=REFRESH_AHEAD_MINUTES)
        return expiry <= threshold

    async def _do_refresh(self, *, reason: str = "scheduled") -> bool:
        """
        Call DhanHQ's generateAccessToken API and store the result.
        Returns True on success, False on failure.
        Retries up to MAX_RETRIES times with RETRY_DELAY_SECONDS between
        attempts (TOTP rotates every 30 s, so a brief wait helps avoid
        "stale TOTP" errors if we're right at a 30-second boundary).
        """
        for attempt in range(1, MAX_RETRIES + 1):
            self._load_runtime_credentials()
            if not self._enabled or not self._totp_obj:
                log.error("[TokenRefresher] Missing runtime TOTP credentials; aborting refresh")
                return False

            totp_code = self._totp_obj.now()  # fresh 6-digit code
            log.info(
                f"[TokenRefresher] Attempt {attempt}/{MAX_RETRIES} "
                f"(reason={reason})"
            )
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(
                        GENERATE_TOKEN_URL,
                        params={
                            "dhanClientId": self._client_id,
                            "pin":          self._pin,
                            "totp":         totp_code,
                        },
                    )

                if resp.status_code == 200:
                    data  = resp.json()
                    token = data.get("accessToken")
                    exp_s = data.get("expiryTime")  # "2026-02-19T23:59:59.000"

                    if not token:
                        log.error(
                            f"[TokenRefresher] Response had no accessToken: {data}"
                        )
                        break

                    # Parse expiry (DhanHQ returns IST, no tz info in string)
                    expiry_dt = _parse_expiry(exp_s)

                    # Store in credential_store (also reconnects WSs)
                    from app.credentials.credential_store import rotate_token
                    await rotate_token(token, expiry=expiry_dt, reconnect=(reason != "startup"))

                    log.info(
                        f"[TokenRefresher] Token refreshed successfully. "
                        f"Expiry: {expiry_dt.isoformat() if expiry_dt else 'unknown'}"
                    )
                    return True

                # Non-200 — log and retry (unless it's a 4xx auth error)
                log.warning(
                    f"[TokenRefresher] HTTP {resp.status_code} from generateAccessToken: "
                    f"{resp.text[:300]}"
                )
                if resp.status_code == 400:
                    # Bad PIN / TOTP — no point retrying immediately; wait for
                    # the next TOTP window (30 s) in case we hit a stale code
                    if attempt < MAX_RETRIES:
                        log.info(
                            f"[TokenRefresher] Waiting {RETRY_DELAY_SECONDS}s "
                            f"before retry (TOTP window boundary)…"
                        )
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue

            except httpx.RequestError as exc:
                log.error(f"[TokenRefresher] Network error: {exc}")
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY_SECONDS)

        log.error(
            f"[TokenRefresher] All {MAX_RETRIES} attempts failed — "
            "token NOT refreshed. Manual intervention may be required."
        )
        return False


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_expiry(exp_s: str | None) -> datetime | None:
    """
    Parse DhanHQ expiryTime string to a UTC-aware datetime.
    DhanHQ returns IST without explicit timezone offset, e.g.:
      "2026-01-01T00:00:00.000"
    We interpret this as IST (UTC+5:30) and convert to UTC.
    """
    if not exp_s:
        return None
    try:
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        # Strip milliseconds if present
        clean = exp_s.split(".")[0]
        naive = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S")
        ist_dt = naive.replace(tzinfo=IST)
        return ist_dt.astimezone(timezone.utc)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[TokenRefresher] Could not parse expiry '{exp_s}': {exc}")
        return None


# ── Singleton ────────────────────────────────────────────────────────────────
token_refresher = TokenRefresher()
