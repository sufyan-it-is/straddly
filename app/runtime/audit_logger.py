"""
app/runtime/audit_logger.py
============================
Non-blocking helper that records user activity rows into `activity_audit_log`.

Usage:
    from app.runtime.audit_logger import log_activity

    asyncio.create_task(log_activity(
        action_type="LOGIN",
        request=request,
        actor_user_id=user.id,
        actor_name=user.name,
        actor_role=user.role,
        status_code=200,
    ))

All parameters are optional — missing values are stored as NULL/empty.
Uses asyncio.create_task internally so callers are never blocked.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import Request

from app.runtime.geoip import lookup as geo_lookup

log = logging.getLogger(__name__)


def _extract_ip(request: Request) -> str:
    """
    Extract client IP, checking X-Forwarded-For first (set by Traefik / Coolify
    reverse proxy), then falling back to direct connection host.
    """
    forwarded = request.headers.get("x-forwarded-for", "").split(",")
    first = forwarded[0].strip() if forwarded else ""
    if first:
        return first
    return (request.client.host if request.client else "") or ""


def _extract_ua(request: Request) -> str:
    return request.headers.get("user-agent", "") or ""


async def _do_log(
    *,
    action_type: str,
    ip_address: str,
    user_agent: str,
    actor_user_id: Optional[str],
    actor_name: Optional[str],
    actor_role: Optional[str],
    subject_user_id: Optional[str],
    subject_name: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    endpoint: Optional[str],
    http_method: Optional[str],
    status_code: Optional[int],
    error_detail: Optional[str],
    metadata: Optional[dict],
) -> None:
    try:
        from app.database import get_pool
        geo = geo_lookup(ip_address)
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO activity_audit_log (
                actor_user_id, actor_name, actor_role,
                subject_user_id, subject_name,
                action_type, resource_type, resource_id,
                endpoint, http_method, status_code, error_detail,
                ip_address, user_agent,
                geo_country, geo_country_code, geo_region, geo_city,
                geo_latitude, geo_longitude,
                metadata
            ) VALUES (
                $1::uuid, $2, $3,
                $4::uuid, $5,
                $6, $7, $8,
                $9, $10, $11, $12,
                $13, $14,
                $15, $16, $17, $18,
                $19, $20,
                $21::jsonb
            )
            """,
            actor_user_id,
            actor_name,
            actor_role,
            subject_user_id,
            subject_name,
            action_type[:80],
            (resource_type or "")[:80],
            (resource_id or "")[:200],
            (endpoint or "")[:255],
            (http_method or "")[:10],
            status_code,
            (error_detail or "")[:500],
            (ip_address or "")[:45],
            (user_agent or "")[:500],
            geo.country,
            geo.country_code,
            geo.region,
            geo.city,
            geo.latitude,
            geo.longitude,
            json.dumps(metadata) if metadata else None,
        )
    except Exception as exc:
        log.debug("audit_logger: insert failed (non-critical): %s", exc)


def log_activity(
    *,
    action_type: str,
    request: Optional[Request] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    actor_name: Optional[str] = None,
    actor_role: Optional[str] = None,
    subject_user_id: Optional[str] = None,
    subject_name: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    http_method: Optional[str] = None,
    status_code: Optional[int] = None,
    error_detail: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> "asyncio.Task":
    """
    Fire-and-forget audit log entry. Returns the asyncio.Task so callers can
    await it in tests, but in production it is always detached.
    """
    if request is not None:
        ip_address = ip_address or _extract_ip(request)
        user_agent = user_agent or _extract_ua(request)
        endpoint = endpoint or str(request.url.path)
        http_method = http_method or request.method

    return asyncio.create_task(_do_log(
        action_type=action_type,
        ip_address=ip_address or "",
        user_agent=user_agent or "",
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        actor_role=actor_role,
        subject_user_id=subject_user_id,
        subject_name=subject_name,
        resource_type=resource_type,
        resource_id=resource_id,
        endpoint=endpoint,
        http_method=http_method,
        status_code=status_code,
        error_detail=error_detail,
        metadata=metadata,
    ))
