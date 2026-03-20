"""
app/runtime/notifications.py
=============================
Thin helper for system notifications shown on the admin dashboard.

Features:
  - Async insert into system_notifications table
  - Lightweight in-memory de-duplication to avoid flooding
  - Automatic pruning to keep the table bounded
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.database import get_pool

log = logging.getLogger(__name__)

# dedupe_key -> last emitted at
_dedupe_cache: dict[str, datetime] = {}


async def add_notification(
    category: str,
    severity: str,
    title: str,
    message: str,
    *,
    details: Optional[Any] = None,
    dedupe_key: Optional[str] = None,
    dedupe_ttl_seconds: int = 300,
    max_rows: int = 500,
) -> bool:
    """Persist a system notification for the admin dashboard.

    Returns True if inserted, False if skipped (deduped or failure).
    """
    try:
        norm_category = (category or "general").lower()[:50]
        norm_severity = (severity or "info").lower()[:20]
        title = title[:200]
        # message can be longer; keep it bounded for safety
        message = message[:1000]

        now = datetime.now(tz=timezone.utc)
        key = dedupe_key or f"{norm_category}:{norm_severity}:{title}:{message}"
        last_seen = _dedupe_cache.get(key)
        if last_seen and (now - last_seen) < timedelta(seconds=dedupe_ttl_seconds):
            return False
        _dedupe_cache[key] = now

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO system_notifications (category, severity, title, message, details)
                VALUES ($1, $2, $3, $4, $5)
                """,
                norm_category,
                norm_severity,
                title,
                message,
                details,
            )

            # Keep the table size bounded so admin queries stay snappy
            await conn.execute(
                """
                DELETE FROM system_notifications
                WHERE id NOT IN (
                  SELECT id FROM system_notifications
                  ORDER BY created_at DESC
                  LIMIT $1
                )
                """,
                max_rows,
            )

        return True
    except Exception as exc:  # best-effort; never raise
        log.warning("Notification insert failed: %s", exc)
        return False
