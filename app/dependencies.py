"""
app/dependencies.py
====================
Shared FastAPI dependencies for authentication and authorization.

Usage:
    from app.dependencies import get_current_user, get_admin_user, CurrentUser

    @router.get("/something")
    async def endpoint(user: CurrentUser = Depends(get_current_user)):
        ...
"""
import logging
from typing import Optional, List

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel

from app.database import get_pool

log = logging.getLogger(__name__)

ADMIN_TAB_PERMISSION_KEYS = [
    "admin_tab_users",
    "admin_tab_payouts",
    "admin_tab_ledger",
    "admin_tab_trade_history",
    "admin_tab_pnl",
    "admin_tab_positions_mis",
    "admin_tab_positions_normal",
    "admin_tab_positions_userwise",
    "admin_tab_payin",
    "admin_tab_detailed_logs",
    "admin_tab_sms_otp_settings",
    "admin_tab_course_enrollments",
    "admin_tab_user_signups",
]


def _effective_permissions(role: str, raw_permissions) -> List[str]:
    if role != "ADMIN":
        return list(raw_permissions or [])
    if raw_permissions is None:
        return list(ADMIN_TAB_PERMISSION_KEYS)
    return [p for p in list(raw_permissions or []) if p in ADMIN_TAB_PERMISSION_KEYS]


# ── Shared user model returned by all auth dependencies ──────────────────────

class CurrentUser(BaseModel):
    id:     str
    name:   str
    mobile: str
    role:   str
    permissions: List[str] = []


# ── Internal: extract raw token string from request headers ──────────────────

async def _resolve_token(
    x_auth:        Optional[str] = Header(None, alias="X-AUTH"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> Optional[str]:
    if x_auth:
        return x_auth.strip()
    if authorization:
        return authorization.replace("Bearer ", "").strip()
    return None


# ── Public dependencies ───────────────────────────────────────────────────────

async def get_current_user(
    token: Optional[str] = Depends(_resolve_token),
) -> CurrentUser:
    """
    Validate the session token (X-AUTH or Bearer) and return the logged-in user.
    Raises HTTP 401 if the token is missing, expired, or invalid.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required — provide X-AUTH or Authorization header.",
        )
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid.",
        )
    return CurrentUser(
        id=str(row["id"]),
        name=row["name"],
        mobile=row["mobile"],
        role=row["role"],
        permissions=_effective_permissions(row["role"], row["admin_tab_permissions"]),
    )


async def get_admin_user(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Require ADMIN or SUPER_ADMIN role. Raises HTTP 403 otherwise."""
    if user.role not in ("ADMIN", "SUPER_ADMIN"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


async def get_super_admin_user(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Require SUPER_ADMIN role. Raises HTTP 403 otherwise."""
    if user.role != "SUPER_ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super-admin access required.",
        )
    return user
