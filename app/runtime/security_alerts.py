"""
app/runtime/security_alerts.py
================================
Evaluates three security alert rules and surfaces findings via the existing
`system_notifications` pipeline (visible on the Admin dashboard).

Rules
─────
A. Unusual activity burst  — many requests from same IP in a short window
B. Impossible travel       — two successful logins from geographically distant
                             IPs in a time span that is physically impossible
C. Repeated OTP failures   — too many failed OTP attempts on the same contact
                             or from the same source IP

All rules are best-effort and non-blocking.  They use the `activity_audit_log`
table (written by audit_logger.py) plus the `otp_verifications` table.

Public API
──────────
    check_burst(ip, actor_user_id?)            → fire-and-forget
    check_impossible_travel(user_id, ip)       → fire-and-forget
    check_otp_failures(contact_value, ip)      → fire-and-forget
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Optional

log = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance between two points in kilometres."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ── Rule A — Unusual activity burst ──────────────────────────────────────────

async def _run_check_burst(ip: str, actor_user_id: Optional[str]) -> None:
    try:
        from app.database import get_pool
        from app.config import get_settings
        from app.runtime.notifications import add_notification

        cfg = get_settings()
        pool = get_pool()
        window = cfg.security_burst_window_seconds
        total_thresh = cfg.security_burst_actions_threshold
        sensitive_thresh = cfg.security_sensitive_burst_threshold

        total_count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM activity_audit_log
            WHERE ip_address = $1
              AND created_at >= NOW() - ($2 || ' seconds')::interval
            """,
            ip,
            str(window),
        )
        if total_count and total_count >= total_thresh:
            severity = "error" if total_count >= total_thresh * 2 else "warning"
            await add_notification(
                category="security",
                severity=severity,
                title=f"Activity burst from IP {ip}",
                message=(
                    f"{total_count} requests from IP {ip} in the last "
                    f"{window // 60} minutes — possible automated attack or scraping."
                ),
                details={"ip": ip, "count": int(total_count), "window_seconds": window,
                         "actor_user_id": actor_user_id},
                dedupe_key=f"burst:ip:{ip}",
                dedupe_ttl_seconds=max(window, 600),
            )
            return

        # Sensitive action burst (login/otp)
        sensitive_count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM activity_audit_log
            WHERE ip_address = $1
              AND action_type IN ('LOGIN', 'LOGIN_FAILED', 'OTP_SEND', 'OTP_VERIFY_FAILED')
              AND created_at >= NOW() - ($2 || ' seconds')::interval
            """,
            ip,
            str(window),
        )
        if sensitive_count and sensitive_count >= sensitive_thresh:
            await add_notification(
                category="security",
                severity="warning",
                title=f"High auth activity from IP {ip}",
                message=(
                    f"{sensitive_count} login/OTP requests from IP {ip} in the last "
                    f"{window // 60} minutes — possible credential enumeration."
                ),
                details={"ip": ip, "sensitive_count": int(sensitive_count),
                         "window_seconds": window, "actor_user_id": actor_user_id},
                dedupe_key=f"burst:sensitive:{ip}",
                dedupe_ttl_seconds=max(window, 600),
            )
    except Exception as exc:
        log.debug("security_alerts.check_burst error (non-critical): %s", exc)


def check_burst(ip: Optional[str], actor_user_id: Optional[str] = None) -> None:
    """Fire-and-forget burst check."""
    if not ip:
        return
    asyncio.create_task(_run_check_burst(ip, actor_user_id))


# ── Rule B — Impossible travel ────────────────────────────────────────────────

async def _run_check_impossible_travel(user_id: str, ip: str) -> None:
    try:
        from app.database import get_pool
        from app.config import get_settings
        from app.runtime.notifications import add_notification
        from app.runtime.geoip import lookup as geo_lookup

        cfg = get_settings()
        pool = get_pool()
        window_min = cfg.security_impossible_travel_window_minutes
        max_speed = cfg.security_impossible_travel_speed_kmh

        # Current geo
        current_geo = geo_lookup(ip)
        if current_geo.latitude is None or current_geo.longitude is None:
            return  # no geo data for this IP — skip

        # Last successful login from a different IP with known geo
        prev = await pool.fetchrow(
            """
            SELECT ip_address, geo_latitude, geo_longitude, geo_city, geo_country, created_at
            FROM activity_audit_log
            WHERE actor_user_id = $1::uuid
              AND action_type = 'LOGIN'
              AND ip_address <> $2
              AND geo_latitude IS NOT NULL
              AND created_at >= NOW() - ($3 || ' minutes')::interval
            ORDER BY created_at DESC
            LIMIT 1
            """,
            user_id,
            ip,
            str(window_min),
        )
        if not prev:
            return

        prev_lat = float(prev["geo_latitude"])
        prev_lon = float(prev["geo_longitude"])
        elapsed_hours = max(
            (asyncio.get_event_loop().time() - asyncio.get_event_loop().time()) + 0.001,
            1 / 3600,  # at least 1 second
        )

        # Re-derive elapsed from DB timestamp
        import datetime
        import asyncpg  # noqa
        now_ts = await pool.fetchval("SELECT NOW()")
        prev_ts = prev["created_at"]
        if hasattr(prev_ts, "utcoffset"):
            elapsed_sec = (now_ts - prev_ts).total_seconds()
        else:
            elapsed_sec = 1.0
        elapsed_hours = max(elapsed_sec / 3600, 0.001)

        distance_km = _haversine_km(
            prev_lat, prev_lon,
            current_geo.latitude, current_geo.longitude,
        )
        implied_speed = distance_km / elapsed_hours

        if implied_speed > max_speed and distance_km > 100:
            prev_loc = f"{prev['geo_city'] or ''}/{prev['geo_country'] or ''}"
            curr_loc = f"{current_geo.city or ''}/{current_geo.country or ''}"
            await add_notification(
                category="security",
                severity="critical",
                title=f"Impossible travel for user {user_id[:8]}…",
                message=(
                    f"User logged in from {curr_loc} ({ip}) but was in {prev_loc} "
                    f"{int(elapsed_sec // 60)} minutes ago — "
                    f"implied speed {int(implied_speed)} km/h ({int(distance_km)} km apart)."
                ),
                details={
                    "user_id": user_id,
                    "prev_ip": prev["ip_address"],
                    "prev_location": prev_loc,
                    "current_ip": ip,
                    "current_location": curr_loc,
                    "distance_km": round(distance_km, 1),
                    "elapsed_minutes": round(elapsed_sec / 60, 1),
                    "implied_speed_kmh": round(implied_speed, 1),
                },
                dedupe_key=f"travel:{user_id}:{ip}",
                dedupe_ttl_seconds=3600,
            )
    except Exception as exc:
        log.debug("security_alerts.check_impossible_travel error (non-critical): %s", exc)


def check_impossible_travel(user_id: Optional[str], ip: Optional[str]) -> None:
    """Fire-and-forget impossible travel check."""
    if not user_id or not ip:
        return
    asyncio.create_task(_run_check_impossible_travel(user_id, ip))


# ── Rule C — Repeated OTP failures ───────────────────────────────────────────

async def _run_check_otp_failures(contact_value: str, ip: str) -> None:
    try:
        from app.database import get_pool
        from app.config import get_settings
        from app.runtime.notifications import add_notification

        cfg = get_settings()
        pool = get_pool()
        window_min = cfg.security_otp_fail_window_minutes
        thresh_contact = cfg.security_otp_fail_threshold_per_contact
        thresh_ip = cfg.security_otp_fail_threshold_per_ip

        # Failures on the contact_value in the rolling window
        fail_contact = await pool.fetchval(
            """
            SELECT COUNT(*) FROM activity_audit_log
            WHERE action_type = 'OTP_VERIFY_FAILED'
              AND metadata->>'contact_value' = $1
              AND created_at >= NOW() - ($2 || ' minutes')::interval
            """,
            contact_value,
            str(window_min),
        )
        if fail_contact and fail_contact >= thresh_contact:
            await add_notification(
                category="security",
                severity="warning",
                title=f"Repeated OTP failures on {contact_value[:4]}****",
                message=(
                    f"{fail_contact} failed OTP attempts on {contact_value[:4]}**** "
                    f"in the last {window_min} minutes — possible OTP enumeration."
                ),
                details={"contact": contact_value, "fail_count": int(fail_contact),
                         "window_minutes": window_min, "source_ip": ip},
                dedupe_key=f"otp_fail:contact:{contact_value}",
                dedupe_ttl_seconds=window_min * 60,
            )

        # Failures from the same IP
        fail_ip = await pool.fetchval(
            """
            SELECT COUNT(*) FROM activity_audit_log
            WHERE action_type = 'OTP_VERIFY_FAILED'
              AND ip_address = $1
              AND created_at >= NOW() - ($2 || ' minutes')::interval
            """,
            ip,
            str(window_min),
        )
        if fail_ip and fail_ip >= thresh_ip:
            await add_notification(
                category="security",
                severity="error",
                title=f"OTP attack from IP {ip}",
                message=(
                    f"{fail_ip} failed OTP attempts from IP {ip} "
                    f"in the last {window_min} minutes — possible automated OTP attack."
                ),
                details={"ip": ip, "fail_count": int(fail_ip), "window_minutes": window_min,
                         "latest_contact": contact_value},
                dedupe_key=f"otp_fail:ip:{ip}",
                dedupe_ttl_seconds=window_min * 60,
            )
    except Exception as exc:
        log.debug("security_alerts.check_otp_failures error (non-critical): %s", exc)


def check_otp_failures(contact_value: Optional[str], ip: Optional[str]) -> None:
    """Fire-and-forget OTP failure check."""
    if not contact_value or not ip:
        return
    asyncio.create_task(_run_check_otp_failures(contact_value, ip))
