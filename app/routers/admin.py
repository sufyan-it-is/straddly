"""
app/routers/admin.py
======================
Admin Dashboard endpoints:
  - Credential rotation (manual token paste + TOTP status/force-refresh)
  - WS status (slot health, connection counts)
  - Mode toggle (LIVE / PAPER)
  - Greeks poller interval override
  - Rate-limit stats
"""
import bcrypt as _bcrypt
import logging
from datetime import datetime, timezone
from typing import Optional
import json
import os
import re
import base64

from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel

from app.dependencies                        import CurrentUser, get_admin_user, get_super_admin_user
from app.credentials.credential_store        import (
    rotate_token,
    get_client_id,
    get_access_token,
    get_token_expiry,
    is_token_expiring_soon,
    get_auth_mode,
    set_auth_mode,
    get_static_credentials,
    is_static_configured,
    update_static_credentials,
    get_totp_credentials,
    is_totp_configured,
    update_totp_credentials,
    get_active_auth_mode,
    get_static_post_self_test,
)
from app.credentials.token_refresher         import token_refresher
from app.market_data.websocket_manager        import ws_manager
from app.market_data.depth_ws_manager         import depth_ws_manager
from app.market_data.greeks_poller            import greeks_poller
from app.market_data.rate_limiter             import dhan_client
from app.market_data.static_auth_monitor      import static_auth_monitor
from app.execution_simulator.execution_engine import set_mock_mode, is_mock_mode
from app.runtime.vps_monitor                  import vps_monitor
import app.instruments.subscription_manager   as _sub_mgr

router = APIRouter(prefix="/admin", tags=["Admin"])
log = logging.getLogger(__name__)

# ── User management constants ──────────────────────────────────────────────────
_ALLOWED_STATUSES       = {"ACTIVE", "PENDING", "SUSPENDED", "BLOCKED"}
_ADMIN_CREATABLE_ROLES  = {"USER"}
_ADMIN_VISIBLE_ROLES    = {"USER"}
_ALL_ROLES              = {"USER", "ADMIN", "SUPER_USER", "SUPER_ADMIN"}

# ── Models ─────────────────────────────────────────────────────────────────

class TokenRotateRequest(BaseModel):
    access_token: str
    reconnect_ws: bool = True


class AuthModeRequest(BaseModel):
    """
    mode: 'auto_totp'  — system auto-generates & renews token via TOTP (default)
          'manual'     — auto-refresh paused; admin pastes token via /credentials/rotate
        'static_ip'  — use API Key + Secret (static IP whitelisted)
    """
    mode: str   # 'auto_totp' | 'manual' | 'static_ip'


class ModeToggleRequest(BaseModel):
    paper_mode: bool


class GreeksIntervalRequest(BaseModel):
    seconds: int


class CreateUserRequest(BaseModel):
    first_name:               str
    last_name:                str           = ""
    email:                    str           = ""
    mobile:                   str
    password:                 str
    role:                     str           = "USER"
    status:                   str           = "PENDING"
    address:                  str           = ""
    country:                  str           = "India"
    state:                    str           = ""
    city:                     str           = ""
    aadhar_number:            str           = ""
    pan_number:               str           = ""
    upi:                      str           = ""
    bank_account:             str           = ""
    ifsc_code:                str           = ""
    brokerage_plan:           str           = ""
    brokerage_plan_equity_id: Optional[int] = None  # Brokerage plan for equity/options
    brokerage_plan_futures_id:Optional[int] = None  # Brokerage plan for futures
    initial_balance:          float         = 0.0
    margin_allotted:          float         = 0.0
    aadhar_doc:               Optional[str] = None
    cancelled_cheque_doc:     Optional[str] = None
    pan_card_doc:             Optional[str] = None


class UpdateUserRequest(BaseModel):
    first_name:               Optional[str]   = None
    last_name:                Optional[str]   = None
    email:                    Optional[str]   = None
    mobile:                   Optional[str]   = None
    password:                 Optional[str]   = None
    role:                     Optional[str]   = None
    status:                   Optional[str]   = None
    address:                  Optional[str]   = None
    country:                  Optional[str]   = None
    state:                    Optional[str]   = None
    city:                     Optional[str]   = None
    aadhar_number:            Optional[str]   = None
    pan_number:               Optional[str]   = None
    upi:                      Optional[str]   = None
    bank_account:             Optional[str]   = None
    ifsc_code:                Optional[str]   = None
    brokerage_plan:           Optional[str]   = None
    brokerage_plan_equity_id: Optional[int]   = None  # Brokerage plan for equity/options
    brokerage_plan_futures_id:Optional[int]   = None  # Brokerage plan for futures
    margin_allotted:          Optional[float] = None
    aadhar_doc:               Optional[str]   = None
    cancelled_cheque_doc:     Optional[str]   = None
    pan_card_doc:             Optional[str]   = None


class AddFundsRequest(BaseModel):
    amount: float
    note:   str = ""


class VPSMonitorStartRequest(BaseModel):
    interval_seconds: int = 5


class SmsOtpSettingsRequest(BaseModel):
    message_central_customer_id: str
    message_central_password: str
    otp_expiry_seconds: int
    otp_resend_cooldown_seconds: int
    otp_max_attempts: int


class PayinSettingsRequest(BaseModel):
    payment_link_url: str = ""
    upi_qr_url: str = ""
    upi_id: str = ""
    upi_merchant_name: str = ""
    bank_name: str = ""
    bank_holder_name: str = ""
    bank_account_holder: str = ""
    bank_account_number: str = ""
    bank_ifsc_code: str = ""
    bank_branch: str = ""


class AdminTabPermissionsRequest(BaseModel):
    permissions: list[str]


_SMS_OTP_DEFAULTS = {
    "message_central_customer_id": "C-44071166CC38423",
    "message_central_password": "Allalone@01",
    "otp_expiry_seconds": 180,
    "otp_resend_cooldown_seconds": 300,
    "otp_max_attempts": 5,
}


_PAYIN_SETTINGS_DEFAULTS = {
    "payment_link_url": "",
    "upi_qr_url": "",
    "upi_id": "stockmarket.payments@upi",
    "upi_merchant_name": "Stock Market Finance Ltd",
    "bank_name": "Bank Limited",
    "bank_holder_name": "Bank Limited",
    "bank_account_holder": "StockMarket Finance Ltd",
    "bank_account_number": "5001234567890",
    "bank_ifsc_code": "HDFC0000123",
    "bank_branch": "HDFCINBB",
}


ADMIN_DASHBOARD_TABS = [
    {
        "id": "users",
        "label": "Users",
        "description": "Manage users and profiles",
        "path": "/users",
        "permission": "admin_tab_users",
    },
    {
        "id": "payouts",
        "label": "Payouts",
        "description": "Review and manage payout requests",
        "path": "/payouts",
        "permission": "admin_tab_payouts",
    },
    {
        "id": "ledger",
        "label": "Ledger",
        "description": "View account ledger entries",
        "path": "/ledger",
        "permission": "admin_tab_ledger",
    },
    {
        "id": "trade-history",
        "label": "Trade History",
        "description": "View historical orders and trades",
        "path": "/trade-history",
        "permission": "admin_tab_trade_history",
    },
    {
        "id": "pandl",
        "label": "P&L",
        "description": "Analyze profit and loss",
        "path": "/pandl",
        "permission": "admin_tab_pnl",
    },
    {
        "id": "positions-mis",
        "label": "P.MIS",
        "description": "MIS positions view",
        "path": "/trade/all-positions",
        "permission": "admin_tab_positions_mis",
    },
    {
        "id": "positions-normal",
        "label": "P.Normal",
        "description": "Normal positions view",
        "path": "/all-positions-normal",
        "permission": "admin_tab_positions_normal",
    },
    {
        "id": "positions-userwise",
        "label": "P.Userwise",
        "description": "Userwise positions view",
        "path": "/all-positions-userwise",
        "permission": "admin_tab_positions_userwise",
    },
    {
        "id": "payin",
        "label": "Payin",
        "description": "View and manage payin transactions",
        "path": "/dashboard?tab=payin",
        "permission": "admin_tab_payin",
    },
    {
        "id": "detailed-logs",
        "label": "Detailed Logs",
        "description": "Access detailed system and activity logs",
        "path": "/dashboard?tab=detailedLogs",
        "permission": "admin_tab_detailed_logs",
    },
    {
        "id": "sms-otp-settings",
        "label": "SMS OTP Settings",
        "description": "Configure SMS OTP provider settings",
        "path": "/dashboard?tab=smsOtp",
        "permission": "admin_tab_sms_otp_settings",
    },
    {
        "id": "course-enrollments",
        "label": "Course Enrollments",
        "description": "Manage user course enrollments",
        "path": "/dashboard?tab=courseEnrollments",
        "permission": "admin_tab_course_enrollments",
    },
    {
        "id": "user-signups",
        "label": "User Signups",
        "description": "Review and manage new user signups",
        "path": "/dashboard?tab=userSignups",
        "permission": "admin_tab_user_signups",
    },
]
ADMIN_TAB_PERMISSION_KEYS = {tab["permission"] for tab in ADMIN_DASHBOARD_TABS}


def _normalize_admin_tab_permissions(raw_permissions) -> list[str]:
    if raw_permissions is None:
        return [tab["permission"] for tab in ADMIN_DASHBOARD_TABS]
    normalized = [p for p in list(raw_permissions or []) if p in ADMIN_TAB_PERMISSION_KEYS]
    seen = set()
    deduped = []
    for perm in normalized:
        if perm in seen:
            continue
        seen.add(perm)
        deduped.append(perm)
    return deduped


def _can_access_target_role(caller: CurrentUser, target_role: str) -> bool:
    if caller.role == "SUPER_ADMIN":
        return True
    return target_role in _ADMIN_VISIBLE_ROLES


def _assert_target_role_access(caller: CurrentUser, target_role: str, action: str = "access this user") -> None:
    if _can_access_target_role(caller, target_role):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"You are not allowed to {action}.",
    )


def _to_positive_int(value, fallback: int) -> int:
    try:
        parsed = int(str(value).strip())
        return parsed if parsed > 0 else fallback
    except Exception:
        return fallback


def _sanitize_sms_otp_payload(payload: SmsOtpSettingsRequest) -> dict:
    customer_id = (payload.message_central_customer_id or "").strip()
    password = (payload.message_central_password or "").strip()
    if not customer_id:
        raise HTTPException(status_code=400, detail="Customer ID is required")
    if not password:
        raise HTTPException(status_code=400, detail="Password is required")

    expiry = _to_positive_int(payload.otp_expiry_seconds, int(_SMS_OTP_DEFAULTS["otp_expiry_seconds"]))
    cooldown = _to_positive_int(payload.otp_resend_cooldown_seconds, int(_SMS_OTP_DEFAULTS["otp_resend_cooldown_seconds"]))
    attempts = _to_positive_int(payload.otp_max_attempts, int(_SMS_OTP_DEFAULTS["otp_max_attempts"]))

    return {
        "message_central_customer_id": customer_id,
        "message_central_password": password,
        "otp_expiry_seconds": expiry,
        "otp_resend_cooldown_seconds": cooldown,
        "otp_max_attempts": attempts,
    }


def _sanitize_payin_settings_payload(payload: PayinSettingsRequest) -> dict:
    return {
        "payment_link_url": (payload.payment_link_url or "").strip(),
        "upi_qr_url": (payload.upi_qr_url or "").strip(),
        "upi_id": (payload.upi_id or "").strip(),
        "upi_merchant_name": (payload.upi_merchant_name or "").strip(),
        "bank_name": (payload.bank_name or "").strip(),
        "bank_holder_name": (payload.bank_holder_name or "").strip(),
        "bank_account_holder": (payload.bank_account_holder or "").strip(),
        "bank_account_number": (payload.bank_account_number or "").strip(),
        "bank_ifsc_code": (payload.bank_ifsc_code or "").strip().upper(),
        "bank_branch": (payload.bank_branch or "").strip(),
    }


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/credentials/rotate")
async def rotate_access_token(req: TokenRotateRequest):
    """Manually paste a new DhanHQ access token (fallback when TOTP is not set)."""
    await rotate_token(req.access_token, reconnect=req.reconnect_ws)
    return {"success": True, "message": "Token rotated."}


@router.get("/credentials")
async def get_credentials(admin: CurrentUser = Depends(get_super_admin_user)):
    """Return current client_id (token masked for safety). Super-admin only."""
    token = get_access_token()
    return {
        "client_id":    get_client_id(),
        "token_masked": f"****{token[-6:]}" if token else "(not set)",
    }


# ── Auth mode (auto_totp ⇄ manual) ──────────────────────────────────────

@router.get("/auth-mode")
async def get_auth_mode_status():
    """
    Returns which auth mode is currently active and what each mode means.
    effective_mode values:
      'auto_totp'  — running normally, token auto-renewed
      'manual'     — admin has paused TOTP; paste token via /credentials/rotate
      'disabled'   — TOTP not configured in .env; manual-only for this instance
    """
    return {
        "effective_mode": token_refresher.effective_mode,
        "db_mode":        get_auth_mode(),
        "totp_configured": token_refresher.is_enabled,
        "description": {
            "auto_totp": "System automatically generates and renews the DhanHQ token using TOTP+PIN. No manual action needed.",
            "manual":    "Auto-refresh is PAUSED. Admin must paste a fresh 24-hour token via POST /admin/credentials/rotate.",
        },
    }


@router.post("/auth-mode")
async def switch_auth_mode_basic(req: AuthModeRequest):
    """
    Switch between auto_totp and manual token auth modes.

    **auto_totp** (default): System generates a new token immediately via TOTP
    and continues renewing it automatically every ~22 hours.

    **manual** (emergency fallback): Pauses the auto-refresh. You must manually
    paste a fresh token via POST /admin/credentials/rotate before the current
    token expires.
    """
    mode = req.mode
    if mode not in ("auto_totp", "manual"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be 'auto_totp' or 'manual'.",
        )

    if mode == "manual":
        await token_refresher.pause()
        await set_auth_mode("manual")
        expiry = get_token_expiry()
        from datetime import timezone as tz
        remaining = ""
        if expiry:
            secs = max(0, int((expiry - __import__('datetime').datetime.now(tz=tz.utc)).total_seconds()))
            remaining = f"{secs // 3600}h {(secs % 3600) // 60}m remaining"
        return {
            "success": True,
            "mode": "manual",
            "message": (
                "Auto token refresh PAUSED. "
                f"Current token expires in {remaining}. "
                "Use POST /admin/credentials/rotate to paste a new token before it expires."
            ),
        }

    # mode == 'auto_totp'
    if not token_refresher.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot switch to auto_totp: TOTP credentials are not configured. "
                "Set DHAN_PIN and DHAN_TOTP_SECRET in .env and restart the service."
            ),
        )
    try:
        await token_refresher.resume()
        await set_auth_mode("auto_totp")
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    expiry = get_token_expiry()
    return {
        "success": True,
        "mode": "auto_totp",
        "message": "Auto token refresh RESUMED. A fresh token was generated immediately.",
        "new_expiry": expiry.isoformat() if expiry else None,
    }


# ── Token status & TOTP force-refresh ───────────────────────────────────

@router.get("/token/status")
async def token_status():
    """Show the current token's expiry, time remaining, and active auth mode."""
    expiry = get_token_expiry()
    now    = datetime.now(tz=timezone.utc)
    if expiry:
        remaining_s = max(0, int((expiry - now).total_seconds()))
        remaining_h = remaining_s // 3600
        remaining_m = (remaining_s % 3600) // 60
    else:
        remaining_s = remaining_h = remaining_m = None

    return {
        "effective_mode":  token_refresher.effective_mode,
        "totp_configured": token_refresher.is_enabled,
        "token_expiry_utc": expiry.isoformat() if expiry else None,
        "time_remaining": (
            f"{remaining_h}h {remaining_m}m" if remaining_s is not None else "unknown"
        ),
        "expiring_soon": is_token_expiring_soon(within_minutes=120),
    }


@router.post("/token/refresh")
async def force_token_refresh():
    """
    Immediately force a TOTP-based token refresh.
    Returns an error if TOTP is not configured.
    """
    if not token_refresher.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "TOTP auto-refresh is not configured. "
                "Set DHAN_PIN and DHAN_TOTP_SECRET in .env to enable it."
            ),
        )
    result = await token_refresher.refresh_now()
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Token refresh failed: {result.get('reason', 'unknown error')}",
        )
    return result


@router.get("/ws/status")
async def ws_status():
    """Current WebSocket slot health summary."""
    live_feed_status = ws_manager.get_status()
    depth_status     = depth_ws_manager.get_status()
    return {
        "live_feed_slots":    live_feed_status,
        "depth_ws":           depth_status,
        "pending_orders":     __import__("app.execution_simulator.order_queue_manager",
                                         fromlist=["pending_count"]).pending_count(),
    }


@router.get("/subscriptions")
async def subscription_stats():
    """Instrument counts per WS slot."""
    stats = _sub_mgr.get_stats()
    return stats


@router.post("/mode")
async def toggle_mode(req: ModeToggleRequest):
    """Switch between PAPER and LIVE (data-only) mode."""
    set_mock_mode(req.paper_mode)
    return {"paper_mode": is_mock_mode()}


@router.get("/mode")
async def get_mode():
    return {"paper_mode": is_mock_mode()}


@router.post("/greeks/interval")
async def set_greeks_interval(req: GreeksIntervalRequest):
    """Override the Greeks poll interval at runtime (min 15s)."""
    if req.seconds < 15:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Minimum Greeks poll interval is 15 seconds (DhanHQ rate limit).",
        )
    await greeks_poller.set_interval(req.seconds)
    return {"greeks_poll_seconds": req.seconds}


@router.post("/close-price/rollover")
async def force_close_price_rollover(
    caller: CurrentUser = Depends(get_super_admin_user),
):
    """
    Force immediate close price rollover (SUPER_ADMIN only).
    Updates market_data.close = LTP for all instruments.
    """
    from app.market_data.close_price_rollover import close_price_rollover
    
    result = await close_price_rollover.force_rollover()
    return {
        "status": "completed",
        "rollover_date": str(result["rollover_date"]),
        "updated_count": result["updated_count"],
        "skipped_count": result["skipped_count"],
    }


@router.post("/subscriptions/rollover")
async def force_subscription_rollover(
    caller: CurrentUser = Depends(get_super_admin_user),
):
    """
    Immediately evict expired instruments from active WS subscriptions
    and send unsubscribe to Dhan (SUPER_ADMIN only).
    Use this to clean up bloated subscription counts without restarting.
    """
    from app.instruments import subscription_manager as sm

    stats_before = sm.get_stats()
    await sm.handle_expiry_rollover()
    stats_after = sm.get_stats()

    evicted = stats_before["total_tokens"] - stats_after["total_tokens"]
    return {
        "status": "completed",
        "tokens_before": stats_before["total_tokens"],
        "tokens_after":  stats_after["total_tokens"],
        "evicted":       evicted,
    }


@router.post("/force-subscription-rollover")
async def force_subscription_rollover_legacy(
    caller: CurrentUser = Depends(get_super_admin_user),
):
    """
    Backward-compatible alias for legacy dashboard clients.
    Canonical endpoint is /admin/subscriptions/rollover.
    """
    return await force_subscription_rollover(caller)


@router.get("/users")
async def list_users(
    caller: CurrentUser = Depends(get_admin_user),
):
    """
    List all users with profile fields + wallet balance.
    SUPER_ADMIN sees everyone; ADMIN sees USER + ADMIN only.
    """
    from app.database import get_pool as _get_pool
    pool = _get_pool()

    base_q = """
        SELECT u.user_no, u.id, u.first_name, u.last_name, u.email,
               u.mobile, u.role, u.status, u.is_active, u.created_at,
             u.admin_tab_permissions,
               u.brokerage_plan,
               u.address, u.country, u.state, u.city,
             u.aadhar_number, u.pan_number, u.upi, u.bank_account, u.ifsc_code,
               (u.aadhar_doc IS NOT NULL)           AS has_aadhar_doc,
               (u.cancelled_cheque_doc IS NOT NULL) AS has_cheque_doc,
               (u.pan_card_doc IS NOT NULL)         AS has_pan_doc,
               COALESCE(pa.balance, 0)              AS wallet_balance,
               COALESCE(pa.margin_allotted, 0)      AS margin_allotted
        FROM users u
        LEFT JOIN paper_accounts pa ON pa.user_id = u.id
    """
    if caller.role == "SUPER_ADMIN":
        rows = await pool.fetch(base_q + "ORDER BY u.user_no")
    else:
        rows = await pool.fetch(
            base_q + "WHERE u.role = ANY($1::text[]) ORDER BY u.user_no",
            list(_ADMIN_VISIBLE_ROLES),
        )

    result = []
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"])
        d["wallet_balance"] = float(d.get("wallet_balance") or 0)
        d["margin_allotted"] = float(d.get("margin_allotted") or 0)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        result.append(d)
    return {"data": result}


@router.get("/admin-access/tabs")
async def get_admin_access_tabs(
    caller: CurrentUser = Depends(get_super_admin_user),
):
    return {"tabs": ADMIN_DASHBOARD_TABS}


@router.get("/admin-access/{admin_user_id}")
async def get_admin_access(
    admin_user_id: str,
    caller: CurrentUser = Depends(get_super_admin_user),
):
    from app.database import get_pool as _get_pool
    pool = _get_pool()

    row = await pool.fetchrow(
        """
        SELECT id, first_name, last_name, name, mobile, role, admin_tab_permissions
        FROM users
        WHERE id = $1::uuid
        """,
        admin_user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Admin user not found.")
    if row["role"] != "ADMIN":
        raise HTTPException(status_code=400, detail="Selected user is not an ADMIN.")

    permissions = _normalize_admin_tab_permissions(row["admin_tab_permissions"])
    return {
        "admin": {
            "id": str(row["id"]),
            "name": (row.get("name") or "").strip(),
            "first_name": (row.get("first_name") or "").strip(),
            "last_name": (row.get("last_name") or "").strip(),
            "mobile": (row.get("mobile") or "").strip(),
            "role": row["role"],
        },
        "permissions": permissions,
        "tabs": ADMIN_DASHBOARD_TABS,
    }


@router.post("/admin-access/{admin_user_id}")
async def save_admin_access(
    admin_user_id: str,
    payload: AdminTabPermissionsRequest,
    caller: CurrentUser = Depends(get_super_admin_user),
):
    from app.database import get_pool as _get_pool
    pool = _get_pool()

    target_role = await pool.fetchval(
        "SELECT role FROM users WHERE id = $1::uuid",
        admin_user_id,
    )
    if not target_role:
        raise HTTPException(status_code=404, detail="Admin user not found.")
    if target_role != "ADMIN":
        raise HTTPException(status_code=400, detail="Selected user is not an ADMIN.")

    seen = set()
    normalized = []
    for permission in payload.permissions:
        if permission not in ADMIN_TAB_PERMISSION_KEYS:
            raise HTTPException(status_code=400, detail=f"Invalid permission: {permission}")
        if permission in seen:
            continue
        seen.add(permission)
        normalized.append(permission)

    await pool.execute(
        "UPDATE users SET admin_tab_permissions = $2::text[] WHERE id = $1::uuid",
        admin_user_id,
        normalized,
    )
    return {
        "success": True,
        "message": "Admin access updated successfully.",
        "permissions": normalized,
    }


# ── Scheduler control (Super Admin) ─────────────────────────────────────────


@router.get("/schedulers")
async def list_schedulers(
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    from app.runtime.scheduler_api import get_scheduler_snapshot

    return await get_scheduler_snapshot()


@router.post("/schedulers/{name}/{action}")
async def control_scheduler(
    name: str,
    action: str,
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    from app.runtime.scheduler_api import scheduler_action

    action = (action or "").lower().strip()
    if action not in ("start", "stop", "refresh", "auto"):
        raise HTTPException(status_code=400, detail="Invalid action")

    res = await scheduler_action(name, action)
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("detail") or "Action failed")
    return res


@router.get("/users/{user_id}")
async def get_user(
    user_id: str,
    caller:  CurrentUser = Depends(get_admin_user),
):
    """Return full user record including base64 document fields."""
    from app.database import get_pool as _get_pool
    pool = _get_pool()
    row = await pool.fetchrow(
        """
        SELECT u.*, COALESCE(pa.balance, 0) AS wallet_balance,
               COALESCE(pa.margin_allotted, 0) AS margin_allotted
        FROM users u
        LEFT JOIN paper_accounts pa ON pa.user_id = u.id
        WHERE u.id = $1::uuid
        """,
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")

    # ADMIN cannot see SUPER_USER / SUPER_ADMIN rows
    _assert_target_role_access(caller, row["role"], action="view this user")

    d = dict(row)
    d["id"] = str(d["id"])
    d["wallet_balance"] = float(d.get("wallet_balance") or 0)
    d["margin_allotted"] = float(d.get("margin_allotted") or 0)
    if d.get("created_at"):
        d["created_at"] = d["created_at"].isoformat()
    # Remove password hash from response
    d.pop("password_hash", None)
    return d


@router.post("/users", status_code=201)
async def create_user(
    req:    CreateUserRequest,
    caller: CurrentUser = Depends(get_admin_user),
):
    """Create a new user.  ADMIN can only assign USER or ADMIN roles."""
    from app.database import get_pool as _get_pool
    pool = _get_pool()

    # Role enforcement
    allowed_roles = _ADMIN_CREATABLE_ROLES if caller.role == "ADMIN" else _ALL_ROLES
    if req.role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"You cannot assign role '{req.role}'.",
        )
    if req.status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{req.status}'.")

    # Mobile uniqueness check
    exists = await pool.fetchval(
        "SELECT 1 FROM users WHERE mobile = $1", req.mobile
    )
    if exists:
        raise HTTPException(status_code=409, detail="Mobile number already registered.")

    if (req.email or "").strip():
        email_exists = await pool.fetchval(
            "SELECT 1 FROM users WHERE lower(email)=lower($1)",
            req.email.strip(),
        )
        if email_exists:
            raise HTTPException(status_code=409, detail="Email already registered.")

    pw_hash   = _bcrypt.hashpw(req.password.encode(), _bcrypt.gensalt()).decode()
    full_name = f"{req.first_name} {req.last_name}".strip()
    is_active = req.status == "ACTIVE"

    async def _ensure_brokerage_plan(
        plan_code: str,
        plan_name: str,
        instrument_group: str,
        flat_fee: float,
        percent_fee: float,
    ) -> int:
        row = await pool.fetchrow(
            """
            INSERT INTO brokerage_plans (plan_code, plan_name, instrument_group, flat_fee, percent_fee)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (plan_code) DO UPDATE
              SET plan_name = EXCLUDED.plan_name
            RETURNING plan_id
            """,
            plan_code,
            plan_name,
            instrument_group,
            float(flat_fee),
            float(percent_fee),
        )
        if row and row.get("plan_id") is not None:
            return int(row["plan_id"])
        # Fallback: plan existed already
        pid = await pool.fetchval("SELECT plan_id FROM brokerage_plans WHERE plan_code=$1", plan_code)
        if not pid:
            raise HTTPException(status_code=500, detail=f"Could not resolve brokerage plan '{plan_code}'.")
        return int(pid)

    def _map_brokerage_label_to_codes(label: str) -> tuple[str, str]:
        raw = (label or "").strip()
        if not raw:
            return ("PLAN_A", "PLAN_A_FUTURES")
        upper = raw.upper()
        if "NIL" in upper:
            return ("PLAN_NIL", "PLAN_NIL_FUTURES")

        # Try to extract a decimal like 0.005, 0.003
        m = re.search(r"0\.(\d{3,6})", raw)
        if m:
            pct = float(f"0.{m.group(1)}")
            if abs(pct - 0.005) < 1e-9:
                return ("PLAN_E", "PLAN_E_FUTURES")
            if abs(pct - 0.003) < 1e-9:
                return ("PLAN_C", "PLAN_C_FUTURES")

        # Fallback to Plan1/Plan2/Plan3 naming
        if upper.startswith("PLAN1"):
            return ("PLAN_E", "PLAN_E_FUTURES")
        if upper.startswith("PLAN2"):
            return ("PLAN_C", "PLAN_C_FUTURES")
        if upper.startswith("PLAN3"):
            return ("PLAN_NIL", "PLAN_NIL_FUTURES")

        return ("PLAN_A", "PLAN_A_FUTURES")

    # Set brokerage plans (UI sends brokerage_plan string; DB uses plan IDs)
    equity_plan_id = req.brokerage_plan_equity_id
    futures_plan_id = req.brokerage_plan_futures_id

    if (not equity_plan_id or not futures_plan_id) and (req.brokerage_plan or "").strip():
        eq_code, fut_code = _map_brokerage_label_to_codes(req.brokerage_plan)
        if not equity_plan_id:
            if eq_code == "PLAN_NIL":
                equity_plan_id = await _ensure_brokerage_plan(
                    "PLAN_NIL",
                    "Plan NIL - Equity/Options - ₹0 (no brokerage)",
                    "EQUITY_OPTIONS",
                    0.0,
                    0.0,
                )
            else:
                equity_plan_id = await pool.fetchval(
                    "SELECT plan_id FROM brokerage_plans WHERE plan_code=$1 AND instrument_group='EQUITY_OPTIONS' LIMIT 1",
                    eq_code,
                )
        if not futures_plan_id:
            if fut_code == "PLAN_NIL_FUTURES":
                futures_plan_id = await _ensure_brokerage_plan(
                    "PLAN_NIL_FUTURES",
                    "Plan NIL - Futures - ₹0 (no brokerage)",
                    "FUTURES",
                    0.0,
                    0.0,
                )
            else:
                futures_plan_id = await pool.fetchval(
                    "SELECT plan_id FROM brokerage_plans WHERE plan_code=$1 AND instrument_group='FUTURES' LIMIT 1",
                    fut_code,
                )
    
    if equity_plan_id is None:
        default_equity = await pool.fetchval(
            "SELECT plan_id FROM brokerage_plans WHERE plan_code = 'PLAN_A' AND instrument_group = 'EQUITY_OPTIONS' LIMIT 1"
        )
        equity_plan_id = default_equity
    
    if futures_plan_id is None:
        default_futures = await pool.fetchval(
            "SELECT plan_id FROM brokerage_plans WHERE plan_code = 'PLAN_A_FUTURES' AND instrument_group = 'FUTURES' LIMIT 1"
        )
        futures_plan_id = default_futures

    row = await pool.fetchrow(
        """
        INSERT INTO users
            (name, first_name, last_name, email, mobile, password_hash,
             role, status, is_active,
             brokerage_plan,
             address, country, state, city,
             aadhar_number, pan_number, upi, bank_account, ifsc_code,
             brokerage_plan_equity_id, brokerage_plan_futures_id,
             aadhar_doc, cancelled_cheque_doc, pan_card_doc)
        VALUES
            ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24)
        RETURNING id, user_no
        """,
        full_name, req.first_name, req.last_name, req.email, req.mobile, pw_hash,
        req.role, req.status, is_active,
        (req.brokerage_plan or "").strip(),
        req.address, req.country, req.state, req.city,
        req.aadhar_number, req.pan_number, req.upi, req.bank_account,
        req.ifsc_code,
        equity_plan_id, futures_plan_id,
        req.aadhar_doc, req.cancelled_cheque_doc, req.pan_card_doc,
    )

    user_id = row["id"]

    # Create paper_accounts row with initial balance
    await pool.execute(
        """
        INSERT INTO paper_accounts (user_id, display_name, balance, margin_allotted)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id, full_name, req.initial_balance, req.margin_allotted,
    )

    # Ledger: opening balance
    opening = float(req.initial_balance or 0)
    await pool.execute(
        """
        INSERT INTO ledger_entries
            (user_id, description, debit, credit, balance_after, created_by, ref_type, ref_id)
        VALUES
            ($1::uuid, $2, $3, $4, $5, $6::uuid, $7, $8)
        """,
        str(user_id),
        "Opening Balance",
        abs(opening) if opening < 0 else None,
        opening if opening > 0 else None,
        opening,
        caller.id,
        "OPENING_BALANCE",
        str(user_id),
    )

    return {"success": True, "id": str(user_id), "user_no": row["user_no"]}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    req:     UpdateUserRequest,
    caller:  CurrentUser = Depends(get_admin_user),
):
    """Update user profile / role / status.  ADMIN cannot promote to SUPER_* roles."""
    from app.database import get_pool as _get_pool
    pool = _get_pool()

    existing = await pool.fetchrow(
        """
        SELECT id, role, status, first_name, last_name, mobile
        FROM users
        WHERE id=$1::uuid
        """,
        user_id,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="User not found.")

    # ADMIN cannot touch SUPER_USER / SUPER_ADMIN rows
    _assert_target_role_access(caller, existing["role"], action="modify this user")

    # Role change validation
    if req.role is not None:
        allowed_roles = _ADMIN_CREATABLE_ROLES if caller.role == "ADMIN" else _ALL_ROLES
        if req.role not in allowed_roles:
            raise HTTPException(status_code=403, detail=f"Cannot assign role '{req.role}'.")

    # Status validation
    if req.status is not None and req.status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{req.status}'.")

    # Build SET clause dynamically — only update provided fields
    fields: dict = {}
    if req.first_name           is not None: fields["first_name"]           = req.first_name
    if req.last_name            is not None: fields["last_name"]            = req.last_name
    if req.email                is not None: fields["email"]                = req.email
    if req.email is not None and (req.email or "").strip():
        email_exists = await pool.fetchval(
            "SELECT 1 FROM users WHERE lower(email)=lower($1) AND id<>$2::uuid",
            req.email.strip(),
            user_id,
        )
        if email_exists:
            raise HTTPException(status_code=409, detail="Email already registered.")
    if req.mobile               is not None:
        existing_mobile = str(existing["mobile"] or "")
        next_mobile = (req.mobile or "").strip()
        if not next_mobile:
            raise HTTPException(status_code=400, detail="Mobile cannot be empty.")
        if next_mobile != existing_mobile:
            exists = await pool.fetchval(
                "SELECT 1 FROM users WHERE mobile = $1 AND id <> $2::uuid",
                next_mobile,
                user_id,
            )
            if exists:
                raise HTTPException(status_code=409, detail="Mobile number already registered.")
        fields["mobile"] = next_mobile
    if req.role                 is not None: fields["role"]                 = req.role
    if req.status               is not None:
        fields["status"]    = req.status
        fields["is_active"] = req.status == "ACTIVE"
    if req.address              is not None: fields["address"]              = req.address
    if req.country              is not None: fields["country"]              = req.country
    if req.state                is not None: fields["state"]                = req.state
    if req.city                 is not None: fields["city"]                 = req.city
    if req.aadhar_number        is not None: fields["aadhar_number"]        = req.aadhar_number
    if req.pan_number           is not None: fields["pan_number"]           = req.pan_number
    if req.upi                  is not None: fields["upi"]                  = req.upi
    if req.bank_account         is not None: fields["bank_account"]         = req.bank_account
    if req.ifsc_code            is not None: fields["ifsc_code"]            = req.ifsc_code
    if req.brokerage_plan       is not None: fields["brokerage_plan"]       = (req.brokerage_plan or "").strip()
    if req.brokerage_plan_equity_id  is not None: fields["brokerage_plan_equity_id"]  = req.brokerage_plan_equity_id
    if req.brokerage_plan_futures_id is not None: fields["brokerage_plan_futures_id"] = req.brokerage_plan_futures_id
    if req.aadhar_doc           is not None: fields["aadhar_doc"]           = req.aadhar_doc
    if req.cancelled_cheque_doc is not None: fields["cancelled_cheque_doc"] = req.cancelled_cheque_doc
    if req.pan_card_doc         is not None: fields["pan_card_doc"]         = req.pan_card_doc

    margin_updated = False
    # Margin (stored on paper_accounts, not users)
    if req.margin_allotted is not None:
        await pool.execute(
            """
            INSERT INTO paper_accounts (user_id, margin_allotted)
            VALUES ($1::uuid, $2)
            ON CONFLICT (user_id) DO UPDATE
                SET margin_allotted = $2
            """,
            user_id,
            float(req.margin_allotted),
        )
        margin_updated = True

    # If UI provided only brokerage_plan label (not explicit IDs), map it to plan IDs.
    if req.brokerage_plan is not None and (req.brokerage_plan_equity_id is None and req.brokerage_plan_futures_id is None):
        label = (req.brokerage_plan or "").strip()
        if label:
            upper = label.upper()
            if "NIL" in upper:
                eq_code, fut_code = "PLAN_NIL", "PLAN_NIL_FUTURES"
            else:
                m = re.search(r"0\.(\d{3,6})", label)
                eq_code, fut_code = "PLAN_A", "PLAN_A_FUTURES"
                if m:
                    pct = float(f"0.{m.group(1)}")
                    if abs(pct - 0.005) < 1e-9:
                        eq_code, fut_code = "PLAN_E", "PLAN_E_FUTURES"
                    elif abs(pct - 0.003) < 1e-9:
                        eq_code, fut_code = "PLAN_C", "PLAN_C_FUTURES"

            if eq_code == "PLAN_NIL":
                eq_id = await pool.fetchval("SELECT plan_id FROM brokerage_plans WHERE plan_code='PLAN_NIL' LIMIT 1")
                if not eq_id:
                    eq_id = await pool.fetchval(
                        """
                        INSERT INTO brokerage_plans (plan_code, plan_name, instrument_group, flat_fee, percent_fee)
                        VALUES ('PLAN_NIL', 'Plan NIL - Equity/Options - ₹0 (no brokerage)', 'EQUITY_OPTIONS', 0.0, 0.0)
                        ON CONFLICT (plan_code) DO NOTHING
                        RETURNING plan_id
                        """
                    )
                fields["brokerage_plan_equity_id"] = int(eq_id)
            else:
                eq_id = await pool.fetchval(
                    "SELECT plan_id FROM brokerage_plans WHERE plan_code=$1 AND instrument_group='EQUITY_OPTIONS' LIMIT 1",
                    eq_code,
                )
                if eq_id:
                    fields["brokerage_plan_equity_id"] = int(eq_id)

            if fut_code == "PLAN_NIL_FUTURES":
                fut_id = await pool.fetchval("SELECT plan_id FROM brokerage_plans WHERE plan_code='PLAN_NIL_FUTURES' LIMIT 1")
                if not fut_id:
                    fut_id = await pool.fetchval(
                        """
                        INSERT INTO brokerage_plans (plan_code, plan_name, instrument_group, flat_fee, percent_fee)
                        VALUES ('PLAN_NIL_FUTURES', 'Plan NIL - Futures - ₹0 (no brokerage)', 'FUTURES', 0.0, 0.0)
                        ON CONFLICT (plan_code) DO NOTHING
                        RETURNING plan_id
                        """
                    )
                fields["brokerage_plan_futures_id"] = int(fut_id)
            else:
                fut_id = await pool.fetchval(
                    "SELECT plan_id FROM brokerage_plans WHERE plan_code=$1 AND instrument_group='FUTURES' LIMIT 1",
                    fut_code,
                )
                if fut_id:
                    fields["brokerage_plan_futures_id"] = int(fut_id)

    if req.password:
        fields["password_hash"] = _bcrypt.hashpw(
            req.password.encode(), _bcrypt.gensalt()
        ).decode()

    # Update name from first+last if either changed.
    # Important: allow explicitly setting empty strings (""), so only fall back when value is None.
    existing_first = existing["first_name"] or ""
    existing_last = existing["last_name"] or ""
    if req.first_name is not None or req.last_name is not None:
        first = req.first_name if req.first_name is not None else existing_first
        last = req.last_name if req.last_name is not None else existing_last
        fields["name"] = f"{first} {last}".strip()

    if not fields:
        if margin_updated:
            return {"success": True, "message": "Margin updated."}
        return {"success": True, "message": "Nothing to update."}

    set_parts = [f"{k} = ${i+2}" for i, k in enumerate(fields)]
    values    = list(fields.values())
    await pool.execute(
        f"UPDATE users SET {', '.join(set_parts)} WHERE id = $1::uuid",
        user_id, *values,
    )
    return {"success": True}


@router.post("/users/{user_id}/funds")
async def add_funds(
    user_id: str,
    req:     AddFundsRequest,
    caller:  CurrentUser = Depends(get_admin_user),
):
    """Credit or debit a user's paper-trading wallet."""
    from app.database import get_pool as _get_pool
    pool = _get_pool()

    target = await pool.fetchrow("SELECT id, role FROM users WHERE id = $1::uuid", user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    _assert_target_role_access(caller, target["role"], action="adjust funds for this user")

    if req.amount == 0:
        raise HTTPException(status_code=400, detail="Amount must be non-zero.")

    await pool.execute(
        """
        INSERT INTO paper_accounts (user_id, balance)
        VALUES ($1::uuid, $2)
        ON CONFLICT (user_id) DO UPDATE
            SET balance = paper_accounts.balance + $2
        """,
        user_id, req.amount,
    )
    new_balance = await pool.fetchval(
        "SELECT balance FROM paper_accounts WHERE user_id=$1::uuid", user_id
    )

    # Ledger entry
    amt = float(req.amount)
    desc = "Funds Adjustment" + (f": {req.note}" if (req.note or "").strip() else "")
    await pool.execute(
        """
        INSERT INTO ledger_entries
            (user_id, description, debit, credit, balance_after, created_by, ref_type, ref_id)
        VALUES
            ($1::uuid, $2, $3, $4, $5, $6::uuid, $7, $8)
        """,
        user_id,
        desc,
        abs(amt) if amt < 0 else None,
        amt if amt > 0 else None,
        float(new_balance or 0),
        caller.id,
        "ADMIN_ADJUSTMENT",
        user_id,
    )
    return {"success": True, "new_balance": float(new_balance or 0)}


@router.get("/brokerage-plans")
@router.get("/brokerage-plans/")
async def get_brokerage_plans():
    """Return the fixed list of brokerage plans."""
    return {"data": BROKERAGE_PLANS}


@router.get("/rate-limits")
async def rate_limit_stats():
    """
    Live DhanHQ REST call stats from the universal DhanHttpClient.
    Shows total calls, throttle events, per-endpoint counts, and
    current-window call counts relative to each limit.
    """
    from app.market_data.rate_limiter import dhan_client
    return dhan_client.get_stats()


# ── Subscription Lists ─────────────────────────────────────────────────────

VALID_LISTS = {"equity", "options_stocks", "futures_stocks", "etf", "mcx_futures", "mcx_options"}


@router.get("/subscription-lists/{list_name}")
async def download_subscription_list(list_name: str):
    """
    Download the current subscription list as a CSV file.
    list_name: equity | options_stocks | futures_stocks | etf | mcx_futures | mcx_options
    """
    if list_name not in VALID_LISTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown list '{list_name}'. Valid: {sorted(VALID_LISTS)}",
        )
    from app.instruments.scrip_master import get_list_as_csv
    from fastapi.responses import Response
    csv_content = await get_list_as_csv(list_name)
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{list_name}.csv"'},
    )


@router.get("/subscription-lists/{list_name}/symbols")
@router.get("/subscription-lists/{list_name}/symbols/")
async def get_subscription_list_symbols(list_name: str):
    """Return the current symbols in a subscription list as JSON."""
    if list_name not in VALID_LISTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown list '{list_name}'. Valid: {sorted(VALID_LISTS)}",
        )
    from app.database import get_pool as _get_pool
    pool = _get_pool()
    rows = await pool.fetch(
        "SELECT symbol FROM subscription_lists WHERE list_name=$1 ORDER BY symbol",
        list_name,
    )
    return {"list_name": list_name, "symbols": [r["symbol"] for r in rows], "count": len(rows)}


@router.post("/subscription-lists/{list_name}")
async def upload_subscription_list(list_name: str, request: Request):
    """
    Replace a subscription list with uploaded CSV content and re-classify instruments.

    The CSV must have a header row. The first data column is used (either display
    names like 'Reliance Industries' or commodity symbols like 'GOLD').

    After replacement the instrument_master tier assignments are updated live.
    """
    if list_name not in VALID_LISTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown list '{list_name}'. Valid: {sorted(VALID_LISTS)}",
        )
    body = await request.body()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Request body must contain CSV text.",
        )
    try:
        csv_text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not decode body as UTF-8.",
        )

    from app.instruments.scrip_master import replace_list_from_csv
    try:
        imported = await replace_list_from_csv(list_name, csv_text)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return {"list_name": list_name, "symbols_imported": imported, "success": True}


# ── Scrip Master ───────────────────────────────────────────────────────────

@router.post("/scrip-master/refresh")
async def manual_scrip_master_refresh(request: Request):
    """
    Manually trigger a fresh download + reload of the DhanHQ instrument master CSV.
    Pass ?local=true to reload from the local file instead of downloading from CDN.
    """
    use_local: bool = request.query_params.get("local", "false").lower() in ("1", "true", "yes")
    from app.instruments.scrip_master import refresh_instruments
    from app.market_data.tick_processor import tick_processor
    await refresh_instruments(download=not use_local)
    tick_processor.clear_meta_cache()
    return {"success": True, "source": "local_file" if use_local else "cdn"}


@router.get("/scrip-master/status")
async def scrip_master_status():
    """Return last CDN refresh timestamp and instrument counts per tier/segment."""
    from app.database import get_pool as _get_pool
    pool = _get_pool()

    # Last refresh time
    row = await pool.fetchrow(
        "SELECT value FROM system_config WHERE key='scrip_master_refreshed_at'",
    )
    refreshed_at = row["value"] if row else None

    # Counts
    counts = await pool.fetch(
        """
        SELECT
            COALESCE(tier, 'excluded') AS tier,
            instrument_type,
            COUNT(*) AS cnt
        FROM instrument_master
        GROUP BY tier, instrument_type
        ORDER BY tier, instrument_type
        """,
    )
    total = await pool.fetchval("SELECT COUNT(*) FROM instrument_master")
    subscribed = await pool.fetchval(
        "SELECT COUNT(*) FROM instrument_master WHERE tier IS NOT NULL",
    )

    return {
        "last_refreshed_at": refreshed_at,
        "total_instruments": total,
        "subscribed_instruments": subscribed,
        "breakdown": [dict(r) for r in counts],
    }


# ── SuperAdmin Dashboard convenience endpoints ────────────────────────────

class CredentialsSaveRequest(BaseModel):
    client_id: str = ""
    access_token: str = ""
    api_key: str = ""
    secret_api: str = ""
    dhan_pin: str = ""
    dhan_totp_secret: str = ""
    daily_token: str = ""
    auth_mode: str = "auto_totp"


class AuthModeSwitchRequest(BaseModel):
    """Request to switch authentication mode."""
    auth_mode: str
    force: bool = False
    daily_token: str = ""


class AuthModeSelfTestResult(BaseModel):
    mode: str
    status: str
    summary: str
    missing_credentials: list[str]
    required_credentials: list[str]


def _normalize_auth_mode(mode: str) -> str:
    """Accept legacy dashboard values and map to internal auth modes."""
    raw = (mode or "").strip().upper()
    if raw in ("AUTO_TOTP", "TOTP", "AUTO"):
        return "auto_totp"
    if raw in ("STATIC_IP", "STATIC"):
        return "static_ip"
    if raw in ("DAILY_TOKEN", "DAILY_MANUAL", "MANUAL"):
        return "manual"
    return (mode or "").strip().lower()


@router.get("/credentials/active")
async def get_credentials_active(admin: CurrentUser = Depends(get_super_admin_user)):
    """Return full credential data for the SuperAdmin dashboard."""
    token = get_access_token()
    expiry = get_token_expiry()
    static_masked = get_static_credentials(masked=True)
    totp_masked = get_totp_credentials(masked=True)
    return {
        "client_id":   get_client_id(),
        "token_masked": f"****{token[-6:]}" if token else None,
        "has_token":   bool(token),
        "auth_mode":   get_auth_mode(),
        "effective_mode": get_active_auth_mode(),
        "last_updated": expiry.isoformat() if expiry else None,
        "static_configured": is_static_configured(),
        "static_client_id": static_masked.get("client_id"),
        "totp_configured": is_totp_configured(),
        "pin_masked": totp_masked.get("pin") or None,
        "totp_secret_masked": totp_masked.get("totp_secret") or None,
        "preferred_order": ["auto_totp", "static_ip", "manual"],
    }


@router.post("/credentials/save")
async def save_credentials(req: CredentialsSaveRequest, admin: CurrentUser = Depends(get_super_admin_user)):
    """Save credentials (client_id + access_token) from SuperAdmin dashboard."""
    from app.credentials.credential_store import update_client_id as _update_client_id
    import traceback
    
    errors = []
    saved_items = []
    did_rotate_token = False
    
    try:
        # Update Client ID
        if req.client_id:
            try:
                await _update_client_id(req.client_id)
                saved_items.append("client_id")
                log.info(f"✅ Client ID updated: {req.client_id[:10]}...")
            except Exception as e:
                error_msg = f"Failed to update client_id: {str(e)}"
                errors.append(error_msg)
                log.error(error_msg)
                log.error(traceback.format_exc())
        
        # Update Static Credentials (API Key + Secret)
        if req.client_id and req.api_key and req.secret_api:
            try:
                await update_static_credentials(
                    static_client_id=req.client_id,
                    api_key=req.api_key,
                    api_secret=req.secret_api,
                )
                saved_items.append("static_credentials")
                log.info(f"✅ Static credentials updated")
            except Exception as e:
                error_msg = f"Failed to update static credentials: {str(e)}"
                errors.append(error_msg)
                log.error(error_msg)
                log.error(traceback.format_exc())

        # Update AUTO_TOTP credentials (PIN + TOTP secret)
        pin = (req.dhan_pin or "").strip()
        totp_secret = (req.dhan_totp_secret or "").strip()
        entered_pin = bool(pin and not all(ch in ("*", "•") for ch in pin))
        entered_totp = bool(totp_secret and not all(ch in ("*", "•") for ch in totp_secret))
        if entered_pin or entered_totp:
            if not (entered_pin and entered_totp):
                errors.append("Provide both DHAN PIN and TOTP secret together to update AUTO_TOTP credentials.")
            else:
                try:
                    await update_totp_credentials(pin=pin, totp_secret=totp_secret)
                    saved_items.append("auto_totp_credentials")
                    log.info("✅ AUTO_TOTP credentials updated")
                except Exception as e:
                    error_msg = f"Failed to update AUTO_TOTP credentials: {str(e)}"
                    errors.append(error_msg)
                    log.error(error_msg)
                    log.error(traceback.format_exc())
        
        # Update Access Token
        token = req.access_token or req.daily_token
        if token and not all(c in ("*", "•") for c in token) and len(token) > 8:
            try:
                # For local ops UX: reconnect WS immediately so changes take effect
                # without requiring a container restart.
                await rotate_token(token, reconnect=True)
                saved_items.append("access_token")
                did_rotate_token = True
                log.info(f"✅ Access token rotated and WebSocket reconnected")
            except Exception as e:
                error_msg = f"Failed to update access token: {str(e)}"
                errors.append(error_msg)
                log.error(error_msg)
                log.error(traceback.format_exc())

        # Safety net: if credentials exist but live-feed slots are still disconnected,
        # or runtime processors/pollers are not running, trigger the same runtime
        # connect flow used by the explicit Connect button. This covers cases where
        # token value is unchanged/masked and rotate path is skipped.
        try:
            if get_client_id() and get_access_token():
                from app.market_data.tick_processor import tick_processor
                from app.market_data.greeks_poller import greeks_poller

                slots = ws_manager.get_status()
                any_connected = any(bool(s.get("connected")) for s in slots)
                needs_runtime_connect = (
                    (not any_connected)
                    or (not tick_processor.is_running)
                    or (not greeks_poller.is_running)
                )
                if needs_runtime_connect:
                    await dhan_connect()
                    if "runtime_connect" not in saved_items:
                        saved_items.append("runtime_connect")
                    if did_rotate_token:
                        log.info("✅ Save fallback: runtime connect verified after token rotation")
                    else:
                        log.info("✅ Save fallback: runtime connect triggered (token unchanged/masked)")
        except Exception as e:
            error_msg = f"Failed to auto-connect Dhan services after save: {str(e)}"
            errors.append(error_msg)
            log.error(error_msg)
            log.error(traceback.format_exc())
        
        # Persist requested auth mode (best effort)
        requested_mode = _normalize_auth_mode(req.auth_mode)
        if requested_mode in ("auto_totp", "static_ip", "manual"):
            try:
                await set_auth_mode(requested_mode)
                if "auth_mode" not in saved_items:
                    saved_items.append("auth_mode")
                log.info("✅ Auth mode set to %s", requested_mode)
            except Exception as e:
                error_msg = f"Failed to set auth mode: {str(e)}"
                errors.append(error_msg)
                log.error(error_msg)
        elif not token_refresher.is_enabled:
            try:
                await set_auth_mode("manual")
                if "auth_mode" not in saved_items:
                    saved_items.append("auth_mode")
                log.info("✅ Auth mode set to manual")
            except Exception as e:
                error_msg = f"Failed to set auth mode: {str(e)}"
                errors.append(error_msg)
                log.error(error_msg)
        
        # Determine response
        if errors:
            if saved_items:
                return {
                    "success": True,
                    "partial": True,
                    "message": f"Partially saved: {', '.join(saved_items)}",
                    "saved": saved_items,
                    "errors": errors,
                    "status": "partial_success"
                }
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to save credentials: {'; '.join(errors)}"
                )
        else:
            return {
                "success": True,
                "partial": False,
                "message": "All credentials saved successfully.",
                "saved": saved_items,
                "status": "success"
            }
    
    except HTTPException:
        raise  # Re-raise HTTPException as-is
    except Exception as e:
        log.error(f"❌ Unexpected error in save_credentials: {str(e)}")
        log.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.post("/auth-mode/switch")
async def switch_auth_mode(req: AuthModeSwitchRequest, admin: CurrentUser = Depends(get_super_admin_user)):
    """
    Switch authentication mode and verify the change.
    
    Modes:
    - 'auto_totp' (primary): auto token generation via TOTP
    - 'static_ip' (secondary): signed REST auth for whitelisted static IP setup
    - 'manual' (fallback): paste daily token manually
    """
    target_mode = _normalize_auth_mode(req.auth_mode)
    
    # Validate mode
    if target_mode not in ("static_ip", "auto_totp", "manual"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid auth_mode: {target_mode!r}. Use 'auto_totp', 'static_ip', or 'manual'.",
        )
    
    current_mode = get_active_auth_mode()
    
    # Already in requested mode?
    if current_mode == target_mode:
        return {
            "success": True,
            "message": f"Already in {target_mode} mode.",
            "mode": target_mode,
            "verification": "no_change",
        }
    
    # Validate preconditions
    if target_mode == "static_ip":
        if not is_static_configured():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Static IP credentials not configured. Save API Key + Secret first.",
            )
    elif target_mode == "auto_totp":
        if not is_totp_configured():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TOTP is not configured. Save DHAN PIN and DHAN TOTP Secret first.",
            )
    elif target_mode == "manual":
        pasted = (req.daily_token or "").strip()
        if pasted and not all(ch in ("*", "•") for ch in pasted) and len(pasted) > 8:
            await rotate_token(pasted, reconnect=True)
        if not get_access_token() and not req.force:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manual mode requires a daily token. Paste token and retry.",
            )
    
    # Set new mode
    await set_auth_mode(target_mode)
    
    # Verify the new mode works
    verification_result = "pending"
    verification_error = None
    
    try:
        if target_mode == "static_ip":
            # Verify via GET /profile call
            resp = await dhan_client.verify_static_auth()
            if resp.status_code == 200:
                verification_result = "success"
            else:
                verification_result = "failed"
                verification_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
                # Fallback to primary mode unless force=True
                if not req.force:
                    await set_auth_mode("auto_totp")
        elif target_mode == "auto_totp":
            try:
                await token_refresher.resume()
                verification_result = "success"
            except RuntimeError as exc:
                verification_result = "failed"
                verification_error = str(exc)
        elif target_mode == "manual":
            await token_refresher.pause()
            verification_result = "success" if get_access_token() else "pending"
            if verification_result == "pending":
                verification_error = "No daily token is currently saved"
    except Exception as exc:
        verification_result = "error"
        verification_error = str(exc)
        # Fallback to previous mode on error
        if not req.force:
            await set_auth_mode(current_mode)
    
    # Reset failure counter on successful mode switch
    if verification_result == "success":
        try:
            await static_auth_monitor.reset_failures()
        except Exception:
            pass
    
    return {
        "success": verification_result in ("success", "pending"),
        "message": f"Switched to {target_mode} mode.",
        "previous_mode": current_mode,
        "current_mode": get_active_auth_mode(),
        "verification": verification_result,
        "error": verification_error,
    }


@router.post("/credentials/switch-mode")
async def switch_auth_mode_compat(req: AuthModeSwitchRequest, admin: CurrentUser = Depends(get_super_admin_user)):
    """Backward-compatible alias used by older dashboard builds."""
    return await switch_auth_mode(req, admin)


@router.get("/auth-status")
async def get_auth_status(admin: CurrentUser = Depends(get_super_admin_user)):
    """
    Return full authentication status for admin diagnostics.
    """
    auth_mode = get_active_auth_mode()
    failure_count = static_auth_monitor.get_failure_count()
    last_failure = static_auth_monitor.get_last_failure_time()
    
    token = get_access_token()
    token_expiry = get_token_expiry()
    static_creds = get_static_credentials(masked=True)
    totp_creds = get_totp_credentials(masked=True)
    
    return {
        "mode": auth_mode,
        "preferred_order": ["auto_totp", "static_ip", "manual"],
        "static_configured": is_static_configured(),
        "static_client_id": static_creds.get("client_id"),
        "totp": {
            "configured": is_totp_configured(),
            "pin_masked": totp_creds.get("pin") or None,
            "totp_secret_masked": totp_creds.get("totp_secret") or None,
        },
        "daily_token": {
            "has_token": bool(token),
            "masked": f"****{token[-6:]}" if token else None,
            "expiry": token_expiry.isoformat() if token_expiry else None,
            "expiring_soon": is_token_expiring_soon(120) if token else False,
        },
        "monitor": {
            "enabled": is_static_configured(),
            "failure_count": failure_count,
            "failure_threshold": 3,
            "last_failure": last_failure.isoformat() if last_failure else None,
            "status": "monitoring" if is_static_configured() else "disabled",
        },
        "token_refresher": {
            "enabled": token_refresher.is_enabled,
            "running": token_refresher._task is not None and not token_refresher._task.done() if token_refresher._task else False,
        },
    }


@router.get("/auth-mode/self-test")
async def auth_mode_self_test(admin: CurrentUser = Depends(get_super_admin_user)):
    """
    Run low-call readiness checks for all auth modes.
    This endpoint intentionally avoids external Dhan API calls to prevent rate-limit noise.
    """
    client_id = (get_client_id() or "").strip()
    daily_token = (get_access_token() or "").strip()
    static_raw = get_static_credentials(masked=False)
    static_client_id = (static_raw.get("client_id") or "").strip()
    static_api_key = (static_raw.get("api_key") or "").strip()
    static_api_secret = (static_raw.get("api_secret") or "").strip()
    totp_ready = bool(is_totp_configured())

    checks: list[AuthModeSelfTestResult] = []

    # AUTO_TOTP requires runtime client ID and saved TOTP/PIN setup.
    auto_missing: list[str] = []
    if not client_id:
        auto_missing.append("client_id")
    if not totp_ready:
        auto_missing.append("DHAN_PIN + DHAN_TOTP_SECRET")
    checks.append(
        AuthModeSelfTestResult(
            mode="auto_totp",
            status="ready" if not auto_missing else "missing",
            summary=(
                "AUTO_TOTP is ready for automatic token generation."
                if not auto_missing
                else "AUTO_TOTP is missing required credentials."
            ),
            missing_credentials=auto_missing,
            required_credentials=["client_id", "DHAN_PIN + DHAN_TOTP_SECRET"],
        )
    )

    # STATIC_IP requires static client-id, key, and secret.
    static_missing: list[str] = []
    if not static_client_id:
        static_missing.append("static_client_id")
    if not static_api_key:
        static_missing.append("dhan_api_key")
    if not static_api_secret:
        static_missing.append("dhan_api_secret")
    checks.append(
        AuthModeSelfTestResult(
            mode="static_ip",
            status="ready" if not static_missing else "missing",
            summary=(
                "STATIC_IP credentials are present for signed REST auth."
                if not static_missing
                else "STATIC_IP is missing required credentials."
            ),
            missing_credentials=static_missing,
            required_credentials=["static_client_id", "dhan_api_key", "dhan_api_secret"],
        )
    )

    # MANUAL requires runtime client ID and a pasted daily access token.
    manual_missing: list[str] = []
    if not client_id:
        manual_missing.append("client_id")
    if not daily_token:
        manual_missing.append("daily_token")
    checks.append(
        AuthModeSelfTestResult(
            mode="manual",
            status="ready" if not manual_missing else "missing",
            summary=(
                "DAILY_MANUAL is ready with a pasted daily token."
                if not manual_missing
                else "DAILY_MANUAL is missing required credentials."
            ),
            missing_credentials=manual_missing,
            required_credentials=["client_id", "daily_token"],
        )
    )

    reenter_recommended: list[str] = []
    if not client_id:
        reenter_recommended.append("client_id")
    if not daily_token:
        reenter_recommended.append("daily_token (only needed for manual fallback)")
    if static_missing:
        reenter_recommended.extend([f"STATIC_IP: {item}" for item in static_missing])
    if not totp_ready:
        reenter_recommended.append("AUTO_TOTP: DHAN_PIN + DHAN_TOTP_SECRET")

    return {
        "success": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "safe_mode": True,
        "active_mode": get_active_auth_mode(),
        "preferred_order": ["auto_totp", "static_ip", "manual"],
        "tests": [item.model_dump() for item in checks],
        "credentials_overview": {
            "client_id_present": bool(client_id),
            "daily_token_present": bool(daily_token),
            "static_credentials_present": bool(static_client_id and static_api_key and static_api_secret),
            "totp_env_present": totp_ready,
        },
        "reenter_recommended": reenter_recommended,
        "static_post_signing": get_static_post_self_test(),
    }


@router.get("/auth-mode/static-post-self-test")
async def auth_mode_static_post_self_test(admin: CurrentUser = Depends(get_super_admin_user)):
    """Targeted self-test for static-IP POST signing parity and client-ID alignment."""
    diagnostic = get_static_post_self_test()
    log.info(
        "[Admin] static post self-test requested: mode=%s status=%s client_id_match=%s payload_sha256=%s",
        diagnostic.get("active_mode"),
        diagnostic.get("status"),
        diagnostic.get("client_id_match"),
        diagnostic.get("payload_sha256"),
    )
    return {
        "success": diagnostic.get("status") == "ready",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        **diagnostic,
    }


@router.post("/auth-mode/reattempt")
async def reattempt_auth_mode():
    """
    Reset failure counter and reattempt the current auth mode.
    Use this after fixing the underlying issue (e.g., IP whitelist restored).
    """
    current_mode = get_active_auth_mode()
    failure_count_before = static_auth_monitor.get_failure_count()
    
    # Reset failure counter
    await static_auth_monitor.reset_failures()
    
    # Verify current mode still works
    verification_result = "pending"
    verification_error = None
    
    try:
        if current_mode == "static_ip":
            resp = await dhan_client.verify_static_auth()
            if resp.status_code == 200:
                verification_result = "success"
            else:
                verification_result = "failed"
                verification_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
        elif current_mode == "auto_totp":
            # Token refresher should work if token is valid
            if get_access_token():
                verification_result = "success"
            else:
                verification_result = "failed"
                verification_error = "No access token available"
    except Exception as exc:
        verification_result = "error"
        verification_error = str(exc)
    
    return {
        "success": verification_result in ("success", "pending"),
        "message": f"Reattempted {current_mode} mode.",
        "mode": current_mode,
        "failure_count_before": failure_count_before,
        "failure_count_after": 0,
        "verification": verification_result,
        "error": verification_error,
    }


@router.get("/vps-monitor/status")
async def get_vps_monitor_status(_: CurrentUser = Depends(get_super_admin_user)):
    """Get current state of manual VPS monitor."""
    return vps_monitor.status()


@router.get("/vps-monitor/samples")
async def get_vps_monitor_samples(
    limit: int = 120,
    _: CurrentUser = Depends(get_super_admin_user),
):
    """Get recent monitor samples for dashboard charts."""
    return vps_monitor.samples(limit=limit)


@router.post("/vps-monitor/start")
async def start_vps_monitor(
    payload: VPSMonitorStartRequest,
    _: CurrentUser = Depends(get_super_admin_user),
):
    """Start VPS monitor manually. It is never auto-started."""
    status_payload = await vps_monitor.start(interval_seconds=payload.interval_seconds)
    return {"success": True, **status_payload}


@router.post("/vps-monitor/stop")
async def stop_vps_monitor(_: CurrentUser = Depends(get_super_admin_user)):
    """Stop VPS monitor manually."""
    status_payload = await vps_monitor.stop()
    return {"success": True, **status_payload}


@router.get("/notifications")
async def get_notifications(
    limit: int = 100,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    unread_only: bool = False,
    _: CurrentUser = Depends(get_super_admin_user),
):
    """Return system notifications for admin dashboard."""
    from app.database import get_pool
    
    pool = get_pool()
    
    # Build query conditionally
    conditions = []
    params = []
    param_idx = 1
    
    if category:
        conditions.append(f"category = ${param_idx}")
        params.append(category)
        param_idx += 1
    
    if severity:
        conditions.append(f"severity = ${param_idx}")
        params.append(severity)
        param_idx += 1
    
    if unread_only:
        conditions.append("read_at IS NULL")
    
    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)
    
    params.append(limit)
    
    query = f"""
        SELECT id, category, severity, title, message, details, 
               created_at, read_at, acknowledged_by, acknowledged_at
        FROM system_notifications
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${param_idx}
    """
    
    rows = await pool.fetch(query, *params)
    
    notifications = []
    for row in rows:
        notifications.append({
            "id": row["id"],
            "category": row["category"],
            "severity": row["severity"],
            "title": row["title"],
            "message": row["message"],
            "details": row["details"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "read_at": row["read_at"].isoformat() if row["read_at"] else None,
            "acknowledged_by": row["acknowledged_by"],
            "acknowledged_at": row["acknowledged_at"].isoformat() if row["acknowledged_at"] else None,
        })
    
    return notifications


@router.get("/market-config")
async def get_market_config():
    """Return current market hours configuration."""
    import json as _json
    from app.database import get_pool as _get_pool
    pool = _get_pool()
    rows = await pool.fetch(
        "SELECT key, value FROM system_config WHERE key LIKE 'market_hours_%'"
    )
    if rows:
        config: dict = {}
        for row in rows:
            exchange = row["key"].replace("market_hours_", "").upper()
            try:
                config[exchange] = _json.loads(row["value"])
            except Exception:
                pass
        if config:
            return config
    return {
        "NSE": {"open": "09:15", "close": "15:30", "days": [0, 1, 2, 3, 4]},
        "BSE": {"open": "09:15", "close": "15:30", "days": [0, 1, 2, 3, 4]},
        "MCX": {"open": "09:00", "close": "23:55", "days": [0, 1, 2, 3, 4]},
    }


@router.post("/market-config")
async def save_market_config(request: Request):
    """Persist market hours configuration."""
    import json as _json
    from app.database import get_pool as _get_pool
    data = await request.json()
    pool = _get_pool()
    for exchange, cfg_val in data.items():
        await pool.execute(
            """
            INSERT INTO system_config (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = now()
            """,
            f"market_hours_{exchange.lower()}",
            _json.dumps(cfg_val),
        )
    return {"success": True}


@router.get("/sms-otp-settings")
async def get_sms_otp_settings(admin: CurrentUser = Depends(get_admin_user)):
    """Return SMS OTP settings from system_config with defaults."""
    from app.database import get_pool as _get_pool

    pool = _get_pool()
    rows = await pool.fetch(
        """
        SELECT key, value
        FROM system_config
        WHERE key = ANY($1::text[])
        """,
        list(_SMS_OTP_DEFAULTS.keys()),
    )
    values = {row["key"]: row["value"] for row in rows}

    return {
        "message_central_customer_id": (values.get("message_central_customer_id") or _SMS_OTP_DEFAULTS["message_central_customer_id"]).strip(),
        "message_central_password": (values.get("message_central_password") or _SMS_OTP_DEFAULTS["message_central_password"]).strip(),
        "otp_expiry_seconds": _to_positive_int(values.get("otp_expiry_seconds"), int(_SMS_OTP_DEFAULTS["otp_expiry_seconds"])),
        "otp_resend_cooldown_seconds": _to_positive_int(values.get("otp_resend_cooldown_seconds"), int(_SMS_OTP_DEFAULTS["otp_resend_cooldown_seconds"])),
        "otp_max_attempts": _to_positive_int(values.get("otp_max_attempts"), int(_SMS_OTP_DEFAULTS["otp_max_attempts"])),
    }


@router.post("/sms-otp-settings")
async def save_sms_otp_settings(payload: SmsOtpSettingsRequest, admin: CurrentUser = Depends(get_admin_user)):
    """Persist SMS OTP settings to system_config."""
    from app.database import get_pool as _get_pool

    values = _sanitize_sms_otp_payload(payload)
    pool = _get_pool()

    for key, value in values.items():
        await pool.execute(
            """
            INSERT INTO system_config (key, value, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                updated_at = now()
            """,
            key,
            str(value),
        )

    return {"success": True, "message": "SMS OTP settings saved"}


@router.get("/payin-settings")
async def get_payin_settings():
    """Return payin settings from system_config with safe defaults."""
    from app.database import get_pool as _get_pool

    pool = _get_pool()
    keys = [f"payin_{key}" for key in _PAYIN_SETTINGS_DEFAULTS.keys()]
    rows = await pool.fetch(
        """
        SELECT key, value
        FROM system_config
        WHERE key = ANY($1::text[])
        """,
        keys,
    )
    values = {row["key"]: row["value"] for row in rows}

    return {
        key: (values.get(f"payin_{key}") or default_value).strip()
        for key, default_value in _PAYIN_SETTINGS_DEFAULTS.items()
    }


@router.post("/payin-settings")
async def save_payin_settings(payload: PayinSettingsRequest, admin: CurrentUser = Depends(get_admin_user)):
    """Persist payin settings to system_config."""
    from app.database import get_pool as _get_pool

    values = _sanitize_payin_settings_payload(payload)
    pool = _get_pool()

    for key, value in values.items():
        await pool.execute(
            """
            INSERT INTO system_config (key, value, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                updated_at = now()
            """,
            f"payin_{key}",
            value,
        )

    return {"success": True, "message": "Payin settings saved"}


@router.post("/reload-scrip-master")
async def reload_scrip_master_alias(request: Request):
    """Alias — reloads instrument master from local CSV (no CDN download)."""
    from app.instruments.scrip_master import refresh_instruments
    from app.market_data.tick_processor import tick_processor
    await refresh_instruments(download=False)
    tick_processor.clear_meta_cache()
    return {"success": True, "message": "Instrument master reloaded from local file."}


@router.post("/diagnose-login")
async def diagnose_login(request: Request):
    """Look up a user by mobile number and return their auth status."""
    from app.database import get_pool as _get_pool
    data = await request.json()
    identifier = data.get("identifier", "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="identifier is required")
    pool = _get_pool()
    row = await pool.fetchrow(
        "SELECT id, name, mobile, role, is_active FROM users WHERE mobile = $1",
        identifier,
    )
    if not row:
        return {"found": False, "detail": "User not found in database."}
    return {
        "found":    True,
        "user_id":  str(row["id"]),
        "name":     row["name"],
        "mobile":   row["mobile"],
        "role":     row["role"],
        "is_active": row["is_active"],
        "detail":   "User found." if row["is_active"] else "User account is inactive.",
    }


@router.post("/backdate-position")
async def backdate_position(
    request: Request,
    admin: CurrentUser = Depends(get_admin_user),
):
    """
    Create or add to a backdated position for a user in the paper trading account.
    Requires ADMIN or SUPER_ADMIN role.
    
    Request body:
    {
      "user_id": "mobile_number_or_uuid",
      "symbol": "LENSKART",
      "qty": 380,
      "price": 514.70,
      "trade_date": "19-02-2026",      // DD-MM-YYYY format
      "trade_time": "09:30",            // HH:MM format, must be within market hours
      "instrument_type": "EQ",          // EQ, FUTSTK, OPTSTK, FUTCOMM, OPTCOMM, etc.
      "exchange": "NSE",                // NSE, BSE, MCX, NCDEX
      "product_type": "MIS"             // MIS or NORMAL
    }
    """
    import uuid
    from app.database import get_pool as _get_pool
    from app.market_hours import is_market_open
    from datetime import datetime, timezone
    
    try:
        data = await request.json()
        
        # Extract and validate inputs
        user_identifier = str(data.get("user_id", "")).strip()
        symbol = data.get("symbol", "").strip()
        qty = int(data.get("qty", 0))
        price = float(data.get("price", 0))
        trade_date_str = data.get("trade_date", "").strip()
        trade_time_str = data.get("trade_time", "").strip()
        instrument_type = data.get("instrument_type", "EQ").strip().upper()
        exchange = data.get("exchange", "NSE").strip().upper()
        product_type = data.get("product_type", "MIS").strip().upper()
        
        # Validate required fields
        if not user_identifier:
            return {"success": False, "detail": "user_id is required"}
        if not symbol:
            return {"success": False, "detail": "symbol is required"}
        if qty <= 0:
            return {"success": False, "detail": "qty must be > 0"}
        if price <= 0:
            return {"success": False, "detail": "price must be > 0"}
        if not trade_date_str:
            return {"success": False, "detail": "trade_date is required (DD-MM-YYYY)"}
        if not trade_time_str:
            return {"success": False, "detail": "trade_time is required (HH:MM)"}
        
        # Parse trade_date from DD-MM-YYYY format
        try:
            trade_dt = datetime.strptime(f"{trade_date_str} {trade_time_str}", "%d-%m-%Y %H:%M")
            opened_at = trade_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return {"success": False, "detail": "trade_date must be DD-MM-YYYY and trade_time must be HH:MM"}
        
        # Validate product_type
        if product_type not in ("MIS", "NORMAL"):
            return {"success": False, "detail": f"product_type must be MIS or NORMAL, got {product_type}"}
        
        # Map instrument types to database values (support commodity options)
        inst_type_map = {
            "EQ": "EQUITY",
            "EQUITY": "EQUITY",
            "FUTSTK": "FUTSTK",
            "OPTSTK": "OPTSTK",
            "FUTIDX": "FUTIDX",
            "OPTIDX": "OPTIDX",
            "FUTCOMM": "FUTCOMM",
            "OPTCOMM": "OPTCOMM",
        }
        inst_type = inst_type_map.get(instrument_type, instrument_type)
        
        # Validate exchange and map to exchange_segment
        valid_exchanges = {"NSE", "BSE", "MCX", "NCDEX"}
        if exchange not in valid_exchanges:
            return {"success": False, "detail": f"Invalid exchange: {exchange}"}

        if exchange in {"MCX", "NCDEX"} and product_type == "MIS":
            return {
                "success": False,
                "detail": "MIS is not allowed for commodity exchanges (MCX/NCDEX). Use NORMAL."
            }
        
        # Map exchange + instrument_type to exchange_segment
        exchange_segment = exchange
        if exchange == "NSE":
            if inst_type in ("EQUITY", "EQ"):
                exchange_segment = "NSE_EQ"
            else:  # Futures/Options
                exchange_segment = "NSE_FNO"
        elif exchange == "BSE":
            if inst_type in ("EQUITY", "EQ"):
                exchange_segment = "BSE_EQ"
            else:
                exchange_segment = "BSE_FO"
        elif exchange == "MCX":
            exchange_segment = "MCX_COMM"
        elif exchange == "NCDEX":
            exchange_segment = "NCDEX_COMM"
        
        pool = _get_pool()
        
        # Lookup user by UUID, mobile, or user_no
        user_row = None
        try:
            # Try UUID first
            user_uuid = uuid.UUID(user_identifier)
            user_row = await pool.fetchrow(
                "SELECT id, role FROM users WHERE id = $1",
                user_uuid
            )
        except (ValueError, Exception):
            # Try mobile
            user_row = await pool.fetchrow(
                "SELECT id, role FROM users WHERE mobile = $1",
                user_identifier
            )
            # If mobile lookup fails and the identifier is numeric, try user_no
            if not user_row and user_identifier.isdigit():
                try:
                    user_no = int(user_identifier)
                    user_row = await pool.fetchrow(
                        "SELECT id, role FROM users WHERE user_no = $1",
                        user_no,
                    )
                except Exception:
                    pass
        
        if not user_row:
            return {"success": False, "detail": f"User not found: {user_identifier}"}

        _assert_target_role_access(admin, user_row["role"], action="create backdated positions for this user")
        
        target_user_id = user_row["id"]
        
        # Lookup instrument (case-insensitive search)
        inst_row = await pool.fetchrow(
            """
            SELECT instrument_token, symbol, exchange_segment
            FROM instrument_master
            WHERE LOWER(symbol) = LOWER($1)
              AND exchange_segment = $2
              AND instrument_type = $3
            LIMIT 1
            """,
            symbol, exchange_segment, inst_type
        )
        
        if not inst_row:
            # Try to find similar symbols to suggest (case-insensitive)
            similar = await pool.fetch(
                """
                SELECT DISTINCT symbol FROM instrument_master
                WHERE LOWER(symbol) LIKE LOWER($1) AND exchange_segment = $2
                LIMIT 3
                """,
                f"{symbol}%", exchange_segment
            )
            suggestions = [row["symbol"] for row in similar]
            suggest_msg = f" Similar symbols: {', '.join(suggestions)}" if suggestions else ""
            return {
                "success": False, 
                "detail": f"Instrument '{symbol}' not found in {exchange} {inst_type}.{suggest_msg} Use the search dropdown to find the correct symbol."
            }
        
        instrument_token = inst_row["instrument_token"]
        
        # Check if user already has an OPEN position in this instrument
        existing = await pool.fetchrow(
            """
            SELECT position_id, quantity, avg_price FROM paper_positions
            WHERE user_id = $1 AND instrument_token = $2 AND status = 'OPEN'
            LIMIT 1
            """,
            target_user_id, instrument_token
        )
        
        if existing:
            # Add to existing position instead of creating new one
            old_qty = existing["quantity"]
            old_avg = float(existing["avg_price"])
            
            # Calculate new weighted average price
            new_qty = old_qty + qty
            new_avg = (old_qty * old_avg + qty * price) / new_qty
            
            try:
                await pool.execute(
                    """
                    UPDATE paper_positions
                    SET quantity = $1, avg_price = $2
                    WHERE position_id = $3
                    """,
                    new_qty, new_avg, existing["position_id"]
                )
                
                return {
                    "success": True,
                    "message": f"Position increased: {old_qty} → {new_qty} {symbol} (avg: {old_avg:.2f} → {new_avg:.2f})",
                    "position": {
                        "position_id": str(existing["position_id"]),
                        "user_id": str(target_user_id),
                        "instrument_token": instrument_token,
                        "symbol": symbol,
                        "quantity": new_qty,
                        "avg_price": new_avg,
                        "status": "OPEN"
                    }
                }
            except Exception as e:
                return {"success": False, "detail": f"Error updating position: {str(e)}"}
        
        # Create new backdated position
        position_id = uuid.uuid4()
        try:
            await pool.execute(
                """
                INSERT INTO paper_positions
                    (position_id, user_id, instrument_token, symbol, exchange_segment,
                     quantity, avg_price, opened_at, product_type, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'OPEN')
                """,
                position_id,
                target_user_id,
                instrument_token,
                symbol,
                exchange_segment,
                qty,
                price,
                opened_at,
                product_type,
            )
        except Exception as e:
            return {"success": False, "detail": f"Database error: {str(e)}"}
        
        return {
            "success": True,
            "message": f"Position created: {qty} {symbol} @ {price} on {trade_date_str} {trade_time_str} ({product_type})",
            "position": {
                "position_id": str(position_id),
                "user_id": str(target_user_id),
                "instrument_token": instrument_token,
                "symbol": symbol,
                "quantity": qty,
                "avg_price": price,
                "opened_at": opened_at.isoformat(),
                "product_type": product_type,
                "status": "OPEN"
            }
        }
        
    except Exception as e:
        return {
            "success": False,
            "detail": f"Error: {str(e)}"
        }


@router.post("/force-exit")
async def force_exit(
    request: Request,
    admin: CurrentUser = Depends(get_admin_user),
):
    """
    Admin endpoint to force-close any user's open position.
    Required fields: user_id, position_id, exit_price, exit_date, exit_time
    """
    from app.database import get_pool
    import uuid as uuid_mod
    from datetime import datetime, timezone
    
    pool = get_pool()
    body = await request.json()
    
    user_id_str = body.get('user_id', '').strip()
    position_id_str = body.get('position_id', '').strip()
    exit_price = body.get('exit_price')
    exit_date_str = body.get('exit_date', '').strip()
    exit_time_str = body.get('exit_time', '').strip()
    
    if not user_id_str or not position_id_str or exit_price is None:
        return {"success": False, "detail": "Missing required fields: user_id, position_id, exit_price"}
    
    if not exit_date_str or not exit_time_str:
        return {"success": False, "detail": "Missing required fields: exit_date (DD-MM-YYYY), exit_time (HH:MM)"}
    
    try:
        exit_price = float(exit_price)
    except (ValueError, TypeError):
        return {"success": False, "detail": "exit_price must be a valid number"}
    
    # Parse exit datetime
    try:
        exit_dt = datetime.strptime(f"{exit_date_str} {exit_time_str}", "%d-%m-%Y %H:%M")
        closed_at = exit_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return {"success": False, "detail": "exit_date must be DD-MM-YYYY and exit_time must be HH:MM"}
    
    # Resolve user_id (could be mobile, UUID, etc.)
    user_row = await pool.fetchrow(
        "SELECT id, role FROM users WHERE CAST(mobile AS TEXT) = $1 OR id = $2::uuid LIMIT 1",
        user_id_str, user_id_str
    )
    
    if not user_row:
        return {"success": False, "detail": f"User '{user_id_str}' not found"}

    _assert_target_role_access(admin, user_row["role"], action="force-exit positions for this user")
    
    uid = user_row['id']
    
    # Find the position (could be by UUID id or by position_id as string)
    try:
        # Try as UUID
        pos_row = await pool.fetchrow(
            "SELECT * FROM paper_positions WHERE id = $1::uuid AND user_id = $2",
            position_id_str, uid
        )
    except:
        # Try as integer or other format
        pos_row = await pool.fetchrow(
            "SELECT * FROM paper_positions WHERE id::text = $1 AND user_id = $2",
            position_id_str, uid
        )
    
    if not pos_row:
        return {"success": False, "detail": f"Position '{position_id_str}' not found for user"}
    
    if pos_row['status'] != 'OPEN':
        return {"success": False, "detail": f"Position is already {pos_row['status']}, cannot close"}
    
    # Close the position
    await pool.execute(
        "UPDATE paper_positions SET status = 'CLOSED', closed_at = $1, exit_price = $2 WHERE id = $3",
        closed_at, exit_price, pos_row['id']
    )
    
    # Log the exit as an order
    order_id = str(uuid_mod.uuid4())
    await pool.execute(
        """
        INSERT INTO paper_orders
            (order_id, user_id, instrument_token, symbol, exchange_segment,
             side, order_type, quantity, fill_price, filled_qty,
             status, product_type, placed_at)
        VALUES ($1, $2, $3, $4, $5, 'SELL', 'MARKET', $6, $7, $6, 'FILLED', $8, $9)
        """,
        order_id, uid, pos_row['instrument_token'], pos_row['symbol'], 
        pos_row.get('exchange_segment', 'NSE_EQ'), 
        pos_row.get('quantity', 0), exit_price, pos_row.get('product_type', 'MIS'), closed_at
    )
    
    return {
        "success": True, 
        "message": f"Position {position_id_str} closed at {exit_price} on {exit_date_str} {exit_time_str}",
        "position_id": str(pos_row['id']),
        "user_id": str(uid),
        "symbol": pos_row['symbol'],
        "quantity": pos_row.get('quantity', 0),
        "exit_price": exit_price,
        "closed_at": closed_at.isoformat()
    }


@router.post("/upload-nse-files")
async def upload_nse_files(request: Request):
    """Stub — NSE margin file upload not yet implemented."""
    return {"success": True, "message": "File upload acknowledged (processing not yet implemented)."}


# ── Dhan runtime connect / disconnect / status ────────────────────────────

@router.get("/dhan/status")
async def dhan_connection_status(admin: CurrentUser = Depends(get_admin_user)):
    """
    Return real-time Dhan connection state without triggering anything.
    Useful for the admin dashboard to poll and show connection health.
    Admin access required.
    """
    from app.market_data.websocket_manager import ws_manager
    from app.market_data.tick_processor    import tick_processor
    from app.credentials.credential_store  import get_client_id, get_access_token

    has_credentials = bool(get_client_id() and get_access_token())
    slots = ws_manager.get_status()
    connected_slots = sum(1 for s in slots if s.get("connected"))
    all_connected = connected_slots == len(slots)
    tick_active   = tick_processor._task is not None and not tick_processor._task.done()

    return {
        "has_credentials": has_credentials,
        "connected":       all_connected,
        "connected_slots": connected_slots,
        "tick_processor":  tick_active,
        "slots":           slots,
    }


@router.post("/dhan/connect")
async def dhan_connect():
    """
    Start all Dhan outbound connections at runtime.
    Safe to call even when DISABLE_DHAN_WS=true — this is an explicit admin override.
    Safe to call repeatedly — already-running tasks are left untouched.
    """
    from app.market_data.websocket_manager import ws_manager
    from app.market_data.depth_ws_manager  import depth_ws_manager
    from app.market_data.tick_processor    import tick_processor
    from app.market_data.greeks_poller     import greeks_poller
    from app.market_hours                  import is_nse_bse_ws_window_open_strict
    from app.credentials.credential_store  import get_client_id, get_access_token, get_active_auth_mode
    from app.credentials.token_refresher   import token_refresher
    from app.instruments import subscription_manager as sm

    if not get_client_id():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Client ID must be saved before connecting.",
        )

    # In primary auto_totp mode we can bootstrap a fresh token at connect-time.
    if not get_access_token() and get_active_auth_mode() == "auto_totp":
        if not token_refresher.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="AUTO_TOTP mode selected but TOTP credentials are not configured.",
            )
        await token_refresher.start()
        refresh = await token_refresher.refresh_now()
        if not refresh.get("success"):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Unable to generate token via TOTP: {refresh.get('reason', 'unknown error')}",
            )

    if not get_access_token():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No daily token available. Paste token manually or switch to auto_totp.",
        )

    started: list[str] = []
    errors: list[str] = []
    
    # 0. Ensure Tier-B subscription map exists (idempotent)
    try:
        stats = sm.get_stats()
        log.info(f"Subscription stats before init: {stats}")
        if (stats.get("total_tokens") or 0) == 0:
            log.info("Tier-B subscription map is empty — initializing...")
            await sm.initialise_tier_b()
            stats_after = sm.get_stats()
            log.info(f"Tier-B initialized — stats: {stats_after}")
            started.append(f"tier_b_init ({stats_after.get('total_tokens', 0)} tokens)")
        else:
            log.info(f"Tier-B already initialized with {stats.get('total_tokens', 0)} tokens")

        tier_a = await sm.initialise_tier_a_from_state()
        if (tier_a.get("loaded") or 0) > 0:
            started.append(f"tier_a_rehydrate ({tier_a.get('loaded')} tokens)")
    except Exception as exc:
        error_msg = f"Tier-B initialization failed: {exc}"
        log.error(error_msg, exc_info=True)
        errors.append(error_msg)
        # Continue — WS will connect but have no instruments subscribed


    # 1. Token refresher (only if TOTP is configured)
    if token_refresher.is_enabled:
        if token_refresher._task is None or token_refresher._task.done():
            await token_refresher.start()
            started.append("token_refresher")

    # 2. Tick processor
    if tick_processor._task is None or tick_processor._task.done():
        await tick_processor.start()
        started.append("tick_processor")

    # 3/4. Dhan WebSocket managers (strict NSE/BSE gate)
    if is_nse_bse_ws_window_open_strict():
        need_ws_start = any(
            conn._task is None or conn._task.done()
            for conn in ws_manager._conns
        )
        if need_ws_start:
            # Stop any partially-started connections first to avoid duplicate tasks
            await ws_manager.stop_all()
            await ws_manager.start_all()
            started.append("ws_manager (5 slots)")

        if depth_ws_manager._task is None or depth_ws_manager._task.done():
            from app.database import get_pool as _get_pool
            from app.config   import get_settings as _get_settings
            _cfg = _get_settings()
            pool = _get_pool()
            depth_rows = await pool.fetch(
                """
                SELECT instrument_token FROM instrument_master
                WHERE underlying = ANY($1::text[])
                  AND instrument_type IN ('FUTIDX','OPTIDX')
                LIMIT 10
                """,
                _cfg.depth_20_underlying,
            )
            depth_tokens = [r["instrument_token"] for r in depth_rows]
            await depth_ws_manager.start(depth_tokens)
            started.append("depth_ws_manager")
    else:
        await ws_manager.stop_all()
        await depth_ws_manager.stop()
        errors.append("Skipped websocket startup: NSE/BSE market window is closed")

    # 5. Greeks poller
    if greeks_poller._task is None or greeks_poller._task.done():
        await greeks_poller.build_skeleton()
        await greeks_poller.start()
        started.append("greeks_poller")

    # Build response
    success = len(errors) == 0 or len(started) > 0
    message_parts = []
    if started:
        message_parts.append(f"Started: {', '.join(started)}")
    else:
        message_parts.append("All services already running — no action taken.")
    if errors:
        message_parts.append(f"Errors: {'; '.join(errors)}")
    
    return {
        "success": success,
        "started": started,
        "errors": errors,
        "message": " | ".join(message_parts),
    }


@router.post("/dhan/disconnect")
async def dhan_disconnect():
    """Stop all Dhan outbound connections."""
    from app.market_data.websocket_manager import ws_manager
    from app.market_data.depth_ws_manager  import depth_ws_manager
    from app.market_data.tick_processor    import tick_processor
    from app.market_data.greeks_poller     import greeks_poller
    from app.credentials.token_refresher   import token_refresher

    await greeks_poller.stop()
    await token_refresher.stop()
    await depth_ws_manager.stop()
    await ws_manager.stop_all()
    await tick_processor.stop()

    return {"success": True, "message": "All Dhan connections stopped."}


@router.get("/dhan/subscriptions")
async def dhan_subscription_diagnostics():
    """
    Diagnostic endpoint — shows subscription map state.
    Returns detailed breakdown of Tier-A/B subscriptions per WS slot.
    """
    from app.instruments import subscription_manager as sm
    from app.database import get_pool
    
    # Get current subscription stats
    stats = sm.get_stats()
    all_active = sm.get_all_active_tokens()
    
    # Count Tier-B instruments in database
    pool = get_pool()
    db_counts = await pool.fetch(
        """
        SELECT tier, COUNT(*) as count, 
               COUNT(*) FILTER (WHERE ws_slot IS NOT NULL) as with_ws_slot
        FROM instrument_master 
        GROUP BY tier
        """
    )
    
    tier_db = {r["tier"]: {"total": r["count"], "with_slot": r["with_ws_slot"]} for r in db_counts}
    
    # Detailed slot breakdown
    slot_details = []
    for slot in range(5):
        tokens = all_active.get(slot, set())
        slot_details.append({
            "slot": slot,
            "subscribed": len(tokens),
            "capacity": 5000,
            "utilization_pct": round(len(tokens) / 50, 1),  # 5000 = 100%
        })
    
    return {
        "subscription_stats": stats,
        "database_tiers": tier_db,
        "slot_details": slot_details,
        "discrepancy": {
            "tier_b_in_db": tier_db.get("B", {}).get("with_slot", 0),
            "tier_b_subscribed": stats.get("total_tokens", 0),
            "missing": max(0, tier_db.get("B", {}).get("with_slot", 0) - stats.get("total_tokens", 0)),
        },
    }


# ── Positions user-wise ────────────────────────────────────────────────────

@router.get("/positions/userwise")
async def positions_userwise(
    current_user: CurrentUser = Depends(get_admin_user),
):
    """
    Returns every user with their per-user P&L summary plus their positions
    (OPEN always shown; CLOSED only if closed_at >= today's start in IST).
    """
    from app.database import get_pool as _get_pool
    pool = _get_pool()

    role_scope_clause = ""
    params = []
    if current_user.role != "SUPER_ADMIN":
        role_scope_clause = "WHERE u.role = ANY($1::text[])"
        params.append(list(_ADMIN_VISIBLE_ROLES))

    rows = await pool.fetch(
        f"""
        WITH ist_today AS (
            -- Start-of-day in IST (UTC+5:30) converted back to UTC for comparison
            SELECT (date_trunc('day', NOW() AT TIME ZONE 'Asia/Kolkata')
                    AT TIME ZONE 'Asia/Kolkata') AS day_start
        ),
        filtered_pos AS (
            SELECT
                pp.*,
                COALESCE(NULLIF(im.lot_size, 0), 1)                           AS lot_size,
                COALESCE(md.ltp, pp.avg_price)                               AS ltp,
                COALESCE((md.ltp - pp.avg_price) * pp.quantity, 0)           AS mtm_calc
            FROM paper_positions pp
            LEFT JOIN instrument_master im ON im.instrument_token = pp.instrument_token
            LEFT JOIN market_data md ON md.instrument_token = pp.instrument_token
            CROSS JOIN ist_today
            WHERE
                pp.status = 'OPEN'
                OR (
                    pp.status = 'CLOSED'
                    AND pp.closed_at IS NOT NULL
                    AND pp.closed_at >= ist_today.day_start
                )
        )
        SELECT
            u.id::text                                                        AS user_id,
            u.user_no,
            COALESCE(
                NULLIF(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.last_name,'')), ''),
                u.name,
                u.mobile
            )                                                                 AS display_name,
            u.mobile,
            COALESCE(pa.balance, 0)                                           AS wallet_balance,
            -- P&L summary
            COALESCE(SUM(fp.realized_pnl), 0)                                AS profit,
            COALESCE(SUM(fp.avg_price * fp.quantity)
                FILTER (WHERE fp.status = 'OPEN'), 0)                        AS trial_by,
            COALESCE(SUM(fp.ltp * fp.quantity)
                FILTER (WHERE fp.status = 'OPEN'), 0)                        AS trial_after,
            pa.balance                                                        AS fund,
            COALESCE(pa.margin_allotted, 0)                                   AS margin_allotted,
            (
                COALESCE(SUM(
                    calculate_position_margin(
                        fp.instrument_token,
                        fp.symbol,
                        fp.exchange_segment,
                        fp.quantity,
                        fp.product_type
                    )
                ) FILTER (WHERE fp.status = 'OPEN' AND fp.quantity != 0), 0)
                + COALESCE(calculate_pending_orders_margin(u.id), 0)
            )                                                                 AS current_margin_usage,
            COALESCE(calculate_pending_orders_margin(u.id), 0)                 AS pending_reserved_margin,
            (
                SELECT COUNT(*)
                FROM paper_orders po
                WHERE po.user_id = u.id
                  AND po.archived_at IS NULL
                  AND po.status::text IN ('PENDING', 'OPEN', 'PARTIAL', 'PARTIAL_FILL', 'PARTIALLY_FILLED')
            )                                                                  AS pending_orders_count,
            COALESCE(SUM(fp.realized_pnl), 0)
                + COALESCE(SUM(fp.mtm_calc) FILTER (WHERE fp.status = 'OPEN'), 0) AS pandl,
            -- Positions JSON
            COALESCE(
                JSON_AGG(
                    JSON_BUILD_OBJECT(
                        'position_id',     fp.position_id,
                        'instrument_token', fp.instrument_token,
                        'symbol',         fp.symbol,
                        'exchange',       fp.exchange_segment,
                        'product_type',   fp.product_type,
                        'lot_size',       fp.lot_size,
                        'quantity',       fp.quantity,
                        'avg_price',      fp.avg_price,
                        'ltp',            fp.ltp,
                        'pnl',            CASE
                                              WHEN fp.status = 'OPEN'
                                              THEN fp.mtm_calc
                                              ELSE fp.realized_pnl
                                          END,
                        'status',         fp.status,
                        'opened_at',      fp.opened_at,
                        'closed_at',      fp.closed_at
                    )
                    ORDER BY fp.opened_at DESC
                ) FILTER (WHERE fp.instrument_token IS NOT NULL),
                '[]'::json
            )                                                                 AS positions
        FROM users u
        LEFT JOIN paper_accounts pa  ON pa.user_id  = u.id
        LEFT JOIN filtered_pos   fp  ON fp.user_id  = u.id
        {role_scope_clause}
        GROUP BY u.id, u.user_no, u.name, u.first_name, u.last_name, u.mobile, pa.balance, pa.margin_allotted
        ORDER BY u.user_no NULLS LAST
        """,
        *params,
    )

    result = []
    for r in rows:
        pandl = float(r["pandl"] or 0)
        fund  = float(r["fund"]  or 0)
        pandl_pct = round(pandl / abs(fund) * 100, 2) if fund else 0.0
        raw_positions = r["positions"]
        if isinstance(raw_positions, list):
            positions = raw_positions
        elif isinstance(raw_positions, str):
            try:
                parsed = json.loads(raw_positions)
                positions = parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                positions = []
        else:
            positions = []

        result.append({
            "user_id":             r["user_id"],
            "user_no":             r["user_no"],
            "display_name":        r["display_name"],
            "mobile":              r["mobile"],
            "wallet_balance":      float(r["wallet_balance"] or 0),
            "profit":              float(r["profit"] or 0),
            "stop_loss":           0.0,
            "trial_by":            float(r["trial_by"] or 0),
            "trial_after":         float(r["trial_after"] or 0),
            "fund":                float(r["fund"] or 0),
            "margin_allotted":       float(r["margin_allotted"] or 0),
            "current_margin_usage":  float(r["current_margin_usage"] or 0),
            "pending_reserved_margin":float(r["pending_reserved_margin"] or 0),
            "pending_orders_count":   int(r["pending_orders_count"] or 0),
            "pandl":                 pandl,
            "pandl_pct":             pandl_pct,
            "positions":             positions,
        })
    return {"data": result}


@router.get("/positions/userwise/{user_id}/active-orders")
async def userwise_active_orders(
    user_id: str,
    current_user: CurrentUser = Depends(get_admin_user),
):
    """Admin/Super-admin: show active (pending/open/partial) orders for a specific user."""
    from app.database import get_pool as _get_pool

    pool = _get_pool()
    target_user = await pool.fetchrow("SELECT id, role FROM users WHERE id = $1::uuid", user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    _assert_target_role_access(current_user, target_user["role"], action="view active orders for this user")

    rows = await pool.fetch(
        """
        SELECT
            po.order_id,
            po.user_id,
            po.instrument_token,
            po.symbol,
            po.exchange_segment,
            po.side,
            po.order_type,
            po.product_type,
            po.status,
            po.quantity,
            COALESCE(po.filled_qty, 0) AS filled_qty,
            GREATEST(po.quantity - COALESCE(po.filled_qty, 0), 0) AS unfilled_qty,
            COALESCE(po.limit_price, po.fill_price) AS price,
            po.trigger_price,
            po.placed_at,
            po.updated_at
        FROM paper_orders po
        WHERE po.user_id = $1::uuid
          AND po.archived_at IS NULL
          AND po.status::text IN ('PENDING', 'OPEN', 'PARTIAL', 'PARTIAL_FILL', 'PARTIALLY_FILLED')
        ORDER BY po.placed_at DESC
        LIMIT 200
        """,
        str(target_user["id"]),
    )

    data = []
    for r in rows:
        data.append({
            "order_id": str(r["order_id"]),
            "user_id": str(r["user_id"]),
            "instrument_token": int(r["instrument_token"] or 0),
            "symbol": r["symbol"],
            "exchange_segment": r["exchange_segment"],
            "side": r["side"],
            "order_type": r["order_type"],
            "product_type": r["product_type"],
            "status": r["status"],
            "quantity": int(r["quantity"] or 0),
            "filled_qty": int(r["filled_qty"] or 0),
            "unfilled_qty": int(r["unfilled_qty"] or 0),
            "price": float(r["price"]) if r["price"] is not None else None,
            "trigger_price": float(r["trigger_price"]) if r["trigger_price"] is not None else None,
            "placed_at": r["placed_at"].isoformat() if r["placed_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        })

    return {"data": data}


# ══════════════════════════════════════════════════════════════════════════════
#  BROKERAGE PLAN MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class BrokeragePlanCreate(BaseModel):
    plan_code: str
    plan_name: str
    instrument_group: str  # 'EQUITY_OPTIONS' or 'FUTURES'
    flat_fee: float = 20.0
    percent_fee: float = 0.0  # 0.002 = 0.2%


class BrokeragePlanUpdate(BaseModel):
    plan_name: Optional[str] = None
    flat_fee: Optional[float] = None
    percent_fee: Optional[float] = None
    is_active: Optional[bool] = None


class UserBrokeragePlanAssign(BaseModel):
    equity_plan_id: Optional[int] = None
    futures_plan_id: Optional[int] = None


@router.get("/brokerage-plans")
@router.get("/brokerage-plans/")
async def list_brokerage_plans(
    current_user: CurrentUser = Depends(get_admin_user),
    active_only: bool = True
):
    """List all brokerage plans."""
    from app.database import get_pool
    pool = get_pool()
    
    query = "SELECT * FROM brokerage_plans"
    if active_only:
        query += " WHERE is_active = TRUE"
    query += " ORDER BY instrument_group, plan_code"
    
    rows = await pool.fetch(query)
    
    return {
        "data": [
            {
                "plan_id": r["plan_id"],
                "plan_code": r["plan_code"],
                "plan_name": r["plan_name"],
                "instrument_group": r["instrument_group"],
                "flat_fee": float(r["flat_fee"]),
                "percent_fee": float(r["percent_fee"]),
                "is_active": r["is_active"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]
    }


@router.post("/brokerage-plans")
@router.post("/brokerage-plans/")
async def create_brokerage_plan(
    plan: BrokeragePlanCreate,
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """Create a new brokerage plan (SUPER_ADMIN only)."""
    from app.database import get_pool
    pool = get_pool()
    
    # Validate instrument_group
    if plan.instrument_group not in ('EQUITY_OPTIONS', 'FUTURES'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="instrument_group must be 'EQUITY_OPTIONS' or 'FUTURES'"
        )
    
    # Check if plan_code already exists
    existing = await pool.fetchrow(
        "SELECT plan_id FROM brokerage_plans WHERE plan_code = $1",
        plan.plan_code
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Plan code '{plan.plan_code}' already exists"
        )
    
    # Insert new plan
    row = await pool.fetchrow(
        """
        INSERT INTO brokerage_plans (plan_code, plan_name, instrument_group, flat_fee, percent_fee)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING plan_id, plan_code, plan_name, instrument_group, flat_fee, percent_fee, is_active, created_at
        """,
        plan.plan_code,
        plan.plan_name,
        plan.instrument_group,
        plan.flat_fee,
        plan.percent_fee
    )
    
    return {
        "success": True,
        "data": {
            "plan_id": row["plan_id"],
            "plan_code": row["plan_code"],
            "plan_name": row["plan_name"],
            "instrument_group": row["instrument_group"],
            "flat_fee": float(row["flat_fee"]),
            "percent_fee": float(row["percent_fee"]),
            "is_active": row["is_active"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
    }


@router.put("/brokerage-plans/{plan_id}")
async def update_brokerage_plan(
    plan_id: int,
    plan: BrokeragePlanUpdate,
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """Update an existing brokerage plan (SUPER_ADMIN only)."""
    from app.database import get_pool
    pool = get_pool()
    
    # Check if plan exists
    existing = await pool.fetchrow(
        "SELECT * FROM brokerage_plans WHERE plan_id = $1",
        plan_id
    )
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Brokerage plan {plan_id} not found"
        )
    
    # Build update query
    updates = []
    params = []
    param_num = 1
    
    if plan.plan_name is not None:
        updates.append(f"plan_name = ${param_num}")
        params.append(plan.plan_name)
        param_num += 1
    
    if plan.flat_fee is not None:
        updates.append(f"flat_fee = ${param_num}")
        params.append(plan.flat_fee)
        param_num += 1
    
    if plan.percent_fee is not None:
        updates.append(f"percent_fee = ${param_num}")
        params.append(plan.percent_fee)
        param_num += 1
    
    if plan.is_active is not None:
        updates.append(f"is_active = ${param_num}")
        params.append(plan.is_active)
        param_num += 1
    
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update"
        )
    
    updates.append(f"updated_at = NOW()")
    params.append(plan_id)
    
    query = f"""
        UPDATE brokerage_plans
        SET {', '.join(updates)}
        WHERE plan_id = ${param_num}
        RETURNING plan_id, plan_code, plan_name, instrument_group, flat_fee, percent_fee, is_active, updated_at
    """
    
    row = await pool.fetchrow(query, *params)
    
    return {
        "success": True,
        "data": {
            "plan_id": row["plan_id"],
            "plan_code": row["plan_code"],
            "plan_name": row["plan_name"],
            "instrument_group": row["instrument_group"],
            "flat_fee": float(row["flat_fee"]),
            "percent_fee": float(row["percent_fee"]),
            "is_active": row["is_active"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
    }


@router.delete("/brokerage-plans/{plan_id}")
async def delete_brokerage_plan(
    plan_id: int,
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """
    Delete (deactivate) a brokerage plan (SUPER_ADMIN only).
    Doesn't actually delete, just marks as inactive.
    """
    from app.database import get_pool
    pool = get_pool()
    
    # Check how many users are using this plan
    user_count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM users 
        WHERE brokerage_plan_equity_id = $1 OR brokerage_plan_futures_id = $1
        """,
        plan_id
    )
    
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete plan: {user_count} user(s) are using it. Reassign users first."
        )
    
    # Mark as inactive instead of deleting
    await pool.execute(
        "UPDATE brokerage_plans SET is_active = FALSE, updated_at = NOW() WHERE plan_id = $1",
        plan_id
    )
    
    return {"success": True, "message": f"Brokerage plan {plan_id} deactivated"}


@router.post("/users/{user_id}/brokerage-plans")
async def assign_user_brokerage_plans(
    user_id: str,
    plans: UserBrokeragePlanAssign,
    current_user: CurrentUser = Depends(get_admin_user),
):
    """Assign brokerage plans to a user."""
    from app.database import get_pool
    pool = get_pool()
    
    # Verify user exists
    user = await pool.fetchrow("SELECT id, role FROM users WHERE id = $1::uuid", user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found"
        )
    _assert_target_role_access(current_user, user["role"], action="assign brokerage plans to this user")
    
    # Verify plans exist
    if plans.equity_plan_id:
        equity_plan = await pool.fetchrow(
            "SELECT plan_code FROM brokerage_plans WHERE plan_id = $1 AND instrument_group = 'EQUITY_OPTIONS'",
            plans.equity_plan_id
        )
        if not equity_plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Equity/Options plan {plans.equity_plan_id} not found"
            )
    
    if plans.futures_plan_id:
        futures_plan = await pool.fetchrow(
            "SELECT plan_code FROM brokerage_plans WHERE plan_id = $1 AND instrument_group = 'FUTURES'",
            plans.futures_plan_id
        )
        if not futures_plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Futures plan {plans.futures_plan_id} not found"
            )
    
    # Update user
    updates = []
    params = []
    param_num = 1
    
    if plans.equity_plan_id is not None:
        updates.append(f"brokerage_plan_equity_id = ${param_num}")
        params.append(plans.equity_plan_id)
        param_num += 1
    
    if plans.futures_plan_id is not None:
        updates.append(f"brokerage_plan_futures_id = ${param_num}")
        params.append(plans.futures_plan_id)
        param_num += 1
    
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No plans specified"
        )
    
    params.append(user_id)
    query = f"UPDATE users SET {', '.join(updates)} WHERE id = ${param_num}::uuid"
    
    await pool.execute(query, *params)
    
    return {"success": True, "message": f"Brokerage plans assigned to user {user_id}"}


# ── User Soft Delete & Archival ──────────────────────────────────────────────

@router.post("/users/{user_id}/soft-delete")
async def soft_delete_user(
    user_id: str,
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """
    Soft delete (archive) a user.
    Sets is_archived = True but keeps all data intact.
    User cannot login after archival.
    """
    from app.database import get_pool
    pool = get_pool()
    
    # Verify user exists
    user = await pool.fetchrow("SELECT id, mobile, name FROM users WHERE id = $1::uuid OR mobile = $1", user_id)
    if not user:
        raise HTTPException(status_code=404, detail=f"User not found")
    
    user_id_uuid = user['id']
    user_display = user['mobile'] or user['name'] or str(user_id_uuid)
    
    # Check if already archived
    archived = await pool.fetchval(
        "SELECT is_archived FROM users WHERE id = $1",
        user_id_uuid
    )
    if archived:
        raise HTTPException(status_code=400, detail=f"User already archived")
    
    # Soft delete: mark as archived
    await pool.execute(
        """
        UPDATE users 
        SET is_archived = TRUE, archived_at = now()
        WHERE id = $1
        """,
        user_id_uuid
    )
    
    log.info(f"User {user_display} (ID: {user_id_uuid}) archived by admin")
    
    return {
        "success": True,
        "message": f"User {user_display} has been archived",
        "user_id": str(user_id_uuid),
        "user_identifier": user_display,
    }


@router.get("/users/archived")
async def get_archived_users(
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """
    Get list of all archived users.
    Shows when they were archived.
    """
    from app.database import get_pool
    pool = get_pool()
    
    archived = await pool.fetch(
        """
        SELECT 
            id::text as user_id,
            mobile,
            name,
            email,
            is_archived,
            archived_at,
            created_at,
            last_login
        FROM users
        WHERE is_archived = TRUE
        ORDER BY archived_at DESC
        """
    )
    
    return {
        "success": True,
        "count": len(archived),
        "archived_users": [dict(row) for row in archived]
    }


# ── User Position Deletion ────────────────────────────────────────────────────

@router.post("/users/{user_id}/positions/delete-all")
async def delete_all_user_positions(
    user_id: str,
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """
    COMPLETELY DELETE ALL DATA related to a user's positions:
    - paper_positions (all)
    - paper_orders (all)
    - paper_trades (all)
    - ledger_entries (all)
    - trade_history (all)
    
    This is irreversible! Used for clearing wrong backdated entries.
    """
    from app.database import get_pool
    pool = get_pool()
    
    # Resolve user_id
    user = await pool.fetchrow(
        "SELECT id, mobile, name FROM users WHERE id = $1::uuid OR mobile = $1",
        user_id
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user_id_uuid = user['id']
    user_display = user['mobile'] or user['name'] or str(user_id_uuid)
    
    # Count before deletion
    pos_count = await pool.fetchval("SELECT COUNT(*) FROM paper_positions WHERE user_id = $1", user_id_uuid)
    orders_count = await pool.fetchval("SELECT COUNT(*) FROM paper_orders WHERE user_id = $1", user_id_uuid)
    trades_count = await pool.fetchval("SELECT COUNT(*) FROM paper_trades WHERE user_id = $1", user_id_uuid)
    ledger_count = await pool.fetchval("SELECT COUNT(*) FROM ledger_entries WHERE user_id = $1", user_id_uuid)
    
    # Delete in correct order (respect foreign keys)
    # Using execute() instead of fetchval() for DELETE statements
    
    # 1. Delete ledger entries
    result_ledger = await pool.execute(
        "DELETE FROM ledger_entries WHERE user_id = $1",
        user_id_uuid
    )
    deleted_ledger = int(result_ledger.split()[-1]) if result_ledger else 0
    
    # 2. Delete paper trades
    result_trades = await pool.execute(
        "DELETE FROM paper_trades WHERE user_id = $1",
        user_id_uuid
    )
    deleted_trades = int(result_trades.split()[-1]) if result_trades else 0
    
    # 3. Delete paper orders
    result_orders = await pool.execute(
        "DELETE FROM paper_orders WHERE user_id = $1",
        user_id_uuid
    )
    deleted_orders = int(result_orders.split()[-1]) if result_orders else 0
    
    # 4. Delete paper positions
    result_positions = await pool.execute(
        "DELETE FROM paper_positions WHERE user_id = $1",
        user_id_uuid
    )
    deleted_positions = int(result_positions.split()[-1]) if result_positions else 0
    
    total_deleted = deleted_ledger + deleted_trades + deleted_orders + deleted_positions
    
    log.warning(
        f"ALL POSITIONS DELETED for user {user_display} "
        f"(ID: {user_id_uuid}). Deleted: {pos_count} positions, "
        f"{orders_count} orders, {trades_count} trades, {ledger_count} ledger entries. "
        f"Total records removed: {total_deleted}"
    )
    
    return {
        "success": True,
        "message": f"All positions and related data deleted for user {user_display}",
        "user_id": str(user_id_uuid),
        "user_identifier": user_display,
        "deleted_summary": {
            "positions": pos_count or 0,
            "orders": orders_count or 0,
            "trades": trades_count or 0,
            "ledger_entries": ledger_count or 0,
            "total": total_deleted
        }
    }


@router.post("/users/{user_id}/positions/delete-specific")
async def delete_specific_user_positions(
    user_id: str,
    request: Request,
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """
    Delete SPECIFIC positions for a user by position IDs.
    
    Request body:
    {
      "position_ids": ["uuid1", "uuid2", ...] or "all"
    }
    
    If position_ids = "all", deletes all positions.
    Otherwise, deletes only the specified positions.
    """
    from app.database import get_pool
    pool = get_pool()
    
    # Resolve user_id (handle both mobile and UUID)
    user = None
    try:
        # Try as UUID first
        import uuid
        uuid_val = uuid.UUID(user_id)
        user = await pool.fetchrow(
            "SELECT id, mobile, name FROM users WHERE id = $1",
            user_id
        )
    except (ValueError, TypeError):
        # Not a UUID, try as mobile
        user = await pool.fetchrow(
            "SELECT id, mobile, name FROM users WHERE mobile = $1",
            user_id
        )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user_id_uuid = user['id']
    user_display = user['mobile'] or user['name'] or str(user_id_uuid)
    
    # Parse request
    try:
        data = await request.json()
        position_ids = data.get("position_ids", [])
    except:
        position_ids = []
    
    # Validate input
    if not position_ids:
        return {
            "success": False,
            "detail": "position_ids is required (list or 'all')"
        }
    
    # If "all", use existing delete-all logic
    if position_ids == "all":
        # Count before deletion
        pos_count = await pool.fetchval("SELECT COUNT(*) FROM paper_positions WHERE user_id = $1", user_id_uuid)
        orders_count = await pool.fetchval("SELECT COUNT(*) FROM paper_orders WHERE user_id = $1", user_id_uuid)
        trades_count = await pool.fetchval("SELECT COUNT(*) FROM paper_trades WHERE user_id = $1", user_id_uuid)
        ledger_count = await pool.fetchval("SELECT COUNT(*) FROM ledger_entries WHERE user_id = $1", user_id_uuid)
        
        # Delete in correct order
        await pool.execute("DELETE FROM ledger_entries WHERE user_id = $1", user_id_uuid)
        await pool.execute("DELETE FROM paper_trades WHERE user_id = $1", user_id_uuid)
        await pool.execute("DELETE FROM paper_orders WHERE user_id = $1", user_id_uuid)
        await pool.execute("DELETE FROM paper_positions WHERE user_id = $1", user_id_uuid)
        
        return {
            "success": True,
            "message": f"All positions deleted for user {user_display}",
            "user_id": str(user_id_uuid),
            "deleted_count": pos_count or 0
        }
    
    # Delete specific positions
    if not isinstance(position_ids, list):
        return {"success": False, "detail": "position_ids must be a list or 'all'"}
    
    if len(position_ids) == 0:
        return {"success": False, "detail": "position_ids list cannot be empty"}
    
    # Convert position_ids strings to UUIDs
    import uuid as uuid_lib
    try:
        position_ids_uuid = [uuid_lib.UUID(str(pid)) for pid in position_ids]
    except (ValueError, TypeError) as e:
        return {"success": False, "detail": f"Invalid position ID format: {str(e)}"}
    
    # Validate that positions belong to this user and get their instrument_tokens
    existing = await pool.fetch(
        "SELECT position_id, instrument_token FROM paper_positions WHERE user_id = $1 AND position_id = ANY($2::uuid[])",
        user_id_uuid,
        position_ids_uuid
    )
    
    if len(existing) != len(position_ids_uuid):
        missing = set(str(p) for p in position_ids_uuid) - {str(p['position_id']) for p in existing}
        return {
            "success": False,
            "detail": f"Some positions not found or don't belong to this user: {list(missing)}"
        }
    
    # Get instrument tokens for the positions to delete related records
    instrument_tokens = [p['instrument_token'] for p in existing]
    position_ids_str = [str(pid) for pid in position_ids_uuid]
    
    # Delete in correct order - related records first
    # 1. Delete related ledger entries (by ref_id matching position_id)
    await pool.execute(
        "DELETE FROM ledger_entries WHERE user_id = $1 AND ref_id = ANY($2::text[])",
        user_id_uuid,
        position_ids_str
    )
    
    # 2. Delete related trades (by instrument_token)
    await pool.execute(
        "DELETE FROM paper_trades WHERE user_id = $1 AND instrument_token = ANY($2::bigint[])",
        user_id_uuid,
        instrument_tokens
    )
    
    # 3. Delete related orders (by instrument_token)
    await pool.execute(
        "DELETE FROM paper_orders WHERE user_id = $1 AND instrument_token = ANY($2::bigint[])",
        user_id_uuid,
        instrument_tokens
    )
    
    # 4. Delete positions
    result = await pool.execute(
        "DELETE FROM paper_positions WHERE user_id = $1 AND position_id = ANY($2::uuid[])",
        user_id_uuid,
        position_ids_uuid
    )
    
    deleted_count = len(position_ids_uuid)
    
    log.warning(
        f"SPECIFIC POSITIONS DELETED for user {user_display} "
        f"(ID: {user_id_uuid}). Deleted {deleted_count} positions: {position_ids}"
    )
    
    return {
        "success": True,
        "message": f"Deleted {deleted_count} position(s) for user {user_display}",
        "user_id": str(user_id_uuid),
        "deleted_count": deleted_count,
        "deleted_position_ids": position_ids
    }


@router.get("/users/{user_id}/positions")
async def get_user_positions(
    user_id: str,
    current_user: CurrentUser = Depends(get_admin_user),
):
    """
    Get all positions (OPEN and CLOSED) for a specific user.
    Returns position list with IDs for selective deletion.
    """
    from app.database import get_pool
    pool = get_pool()
    
    # Resolve user_id (handle both mobile and UUID)
    user = None
    try:
        # Try as UUID first
        import uuid
        uuid_val = uuid.UUID(user_id)
        user = await pool.fetchrow(
            "SELECT id, mobile, name, role FROM users WHERE id = $1",
            user_id
        )
    except (ValueError, TypeError):
        # Not a UUID, try as mobile
        user = await pool.fetchrow(
            "SELECT id, mobile, name, role FROM users WHERE mobile = $1",
            user_id
        )
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    _assert_target_role_access(current_user, user["role"], action="view positions for this user")
    
    user_id_uuid = user['id']
    
    # Get positions
    positions = await pool.fetch(
        """
        SELECT
            position_id,
            symbol,
            quantity,
            avg_price,
            status,
            opened_at,
            closed_at,
            product_type,
            exchange_segment,
            instrument_token
        FROM paper_positions
        WHERE user_id = $1
        ORDER BY opened_at DESC
        """,
        user_id_uuid
    )
    
    result = []
    for p in positions:
        result.append({
            "position_id": str(p["position_id"]),
            "symbol": p["symbol"],
            "quantity": p["quantity"],
            "avg_price": float(p["avg_price"]) if p["avg_price"] else 0.0,
            "status": p["status"],
            "opened_at": p["opened_at"].isoformat() if p["opened_at"] else None,
            "closed_at": p["closed_at"].isoformat() if p["closed_at"] else None,
            "product_type": p["product_type"] or "MIS",
            "exchange": p["exchange_segment"],
        })
    
    return {
        "user_id": str(user_id_uuid),
        "display_name": user['mobile'] or user['name'],
        "positions": result
    }


@router.get("/charge-calculation/status")
async def charge_calculation_status(
    current_user: CurrentUser = Depends(get_admin_user),
):
    """Get charge calculation scheduler status."""
    from app.schedulers.charge_calculation_scheduler import charge_calculation_scheduler
    return {"data": charge_calculation_scheduler.get_stats()}


@router.post("/charge-calculation/run")
async def run_charge_calculation(
    current_user: CurrentUser = Depends(get_admin_user),
    exchanges: Optional[str] = None  # Comma-separated: "NSE,BSE" or "MCX"
):
    """Manually trigger charge calculation."""
    from app.schedulers.charge_calculation_scheduler import charge_calculation_scheduler
    
    exchange_list = exchanges.split(',') if exchanges else None
    result = await charge_calculation_scheduler.run_once(exchanges=exchange_list)
    
    return {"success": True, "data": result}


@router.post("/charge-calculation/recompute-historic")
async def recompute_historic_charge_calculation(
    current_user: CurrentUser = Depends(get_admin_user),
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    user_id: Optional[str] = None,
    include_zero: bool = True,
    include_uniform_360: bool = True,
):
    """
    Force-reset stale historical charge rows and recompute in one call.

    Defaults target known-bad rows:
    - trade_expense = 0.00
    - trade_expense = 3.60 (uniform stale value)
    """
    from app.database import get_pool
    from app.schedulers.charge_calculation_scheduler import charge_calculation_scheduler

    pool = get_pool()
    if pool is None:
        raise HTTPException(status_code=500, detail="Database pool not initialized")

    stale_conditions = []
    if include_zero:
        stale_conditions.append("COALESCE(pp.trade_expense, 0) = 0")
    if include_uniform_360:
        stale_conditions.append("pp.trade_expense = 3.60")

    if not stale_conditions:
        raise HTTPException(status_code=400, detail="At least one stale condition must be enabled")

    closed_from = None
    closed_to = None
    try:
        if from_date:
            fd = datetime.fromisoformat(from_date).date()
            closed_from = datetime(fd.year, fd.month, fd.day, 0, 0, 0)
        if to_date:
            td = datetime.fromisoformat(to_date).date()
            closed_to = datetime(td.year, td.month, td.day, 23, 59, 59)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    where_parts = [
        "pp.status = 'CLOSED'",
        "pp.closed_at IS NOT NULL",
        "(" + " OR ".join(stale_conditions) + ")",
    ]
    params = []

    if user_id:
        target_row = await pool.fetchrow("SELECT role FROM users WHERE id = $1::uuid", str(user_id))
        if not target_row:
            raise HTTPException(status_code=404, detail="User not found")
        _assert_target_role_access(current_user, target_row["role"], action="recompute charges for this user")

        params.append(str(user_id))
        where_parts.append(f"pp.user_id = ${len(params)}::uuid")

    if closed_from is not None:
        params.append(closed_from)
        where_parts.append(f"pp.closed_at >= ${len(params)}")

    if closed_to is not None:
        params.append(closed_to)
        where_parts.append(f"pp.closed_at <= ${len(params)}")

    reset_sql = f"""
        WITH updated AS (
            UPDATE paper_positions pp
            SET charges_calculated = FALSE,
                charges_calculated_at = NULL
            WHERE {' AND '.join(where_parts)}
            RETURNING 1
        )
        SELECT COUNT(*) FROM updated
    """

    reset_count = await pool.fetchval(reset_sql, *params)

    recalc_result = await charge_calculation_scheduler.run_once(
        exchanges=None,
        user_id=str(user_id) if user_id else None,
        closed_from=closed_from,
        closed_to=closed_to,
    )

    return {
        "success": True,
        "data": {
            "reset_rows": int(reset_count or 0),
            "recalculation": recalc_result,
            "filters": {
                "user_id": user_id,
                "from_date": from_date,
                "to_date": to_date,
                "include_zero": include_zero,
                "include_uniform_360": include_uniform_360,
            },
        },
    }


# ── Logo Management ─────────────────────────────────────────────────────────

@router.post("/logo/upload")
async def upload_logo(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """Upload a logo image (SUPER_ADMIN only). Stores as base64 in system_config."""
    # Validate file type
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only image files are allowed (jpg, png, svg, etc.)"
        )
    
    # Read file content
    content = await file.read()
    
    # Validate file size (max 2MB)
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logo file must be smaller than 2MB"
        )
    
    # Convert to base64
    base64_data = base64.b64encode(content).decode('utf-8')
    data_uri = f"data:{file.content_type};base64,{base64_data}"
    
    # Store in database
    from app.database import get_pool
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO system_config (key, value, updated_at)
        VALUES ('logo_data_uri', $1, now())
        ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = now()
        """,
        data_uri
    )
    
    return {
        "success": True,
        "message": "Logo uploaded successfully",
        "filename": file.filename,
        "size": len(content),
    }


@router.get("/logo")
async def get_logo():
    """Retrieve the current logo data URI (public endpoint)."""
    from app.database import get_pool
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT value FROM system_config WHERE key = 'logo_data_uri'"
    )
    
    if not row or not row['value']:
        return {"logo": None}
    
    return {"logo": row['value']}


@router.delete("/logo")
async def delete_logo(current_user: CurrentUser = Depends(get_super_admin_user)):
    """Delete the current logo (SUPER_ADMIN only)."""
    from app.database import get_pool
    pool = get_pool()
    await pool.execute(
        "DELETE FROM system_config WHERE key = 'logo_data_uri'"
    )
    
    return {"success": True, "message": "Logo deleted successfully"}


# ── Option Chain ATM Controls ────────────────────────────────────────────────

_OC_STRIKE_INTERVALS = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}
_OC_UNDERLYINGS      = ["NIFTY", "BANKNIFTY", "SENSEX"]


def _extract_ltp_from_marketfeed_payload(payload: dict, security_id: int) -> float | None:
    """
    Best-effort extractor for Dhan marketfeed/ltp responses.
    Handles common response shapes returned by different account modes.
    """
    if not isinstance(payload, dict):
        return None

    data = payload.get("data")
    candidates = []
    sid_s = str(security_id)

    if isinstance(data, dict):
        # Documented Dhan shape for marketfeed/ltp:
        # {"data": {"IDX_I": {"51": {"last_price": ...}}, ...}, "status": "success"}
        for seg_val in data.values():
            if not isinstance(seg_val, dict):
                continue
            for token_key, token_payload in seg_val.items():
                if str(token_key) != sid_s:
                    continue
                if isinstance(token_payload, dict):
                    for key in ("last_price", "lastTradedPrice", "lastPrice", "ltp"):
                        val = token_payload.get(key)
                        try:
                            f = float(val)
                            if f > 0:
                                return f
                        except Exception:
                            continue

        if isinstance(data.get("quotes"), list):
            candidates.extend(data.get("quotes") or [])
        if isinstance(data.get("data"), list):
            candidates.extend(data.get("data") or [])
        if isinstance(data.get("ltp"), list):
            candidates.extend(data.get("ltp") or [])

    if isinstance(payload.get("data"), list):
        candidates.extend(payload.get("data") or [])

    if isinstance(payload.get("ltp"), list):
        candidates.extend(payload.get("ltp") or [])

    for row in candidates:
        if not isinstance(row, dict):
            continue
        row_sid = row.get("securityId") or row.get("SecurityId") or row.get("security_id")
        if row_sid is not None and str(row_sid) != sid_s:
            continue
        for key in ("lastTradedPrice", "last_price", "lastPrice", "ltp"):
            val = row.get(key)
            try:
                f = float(val)
                if f > 0:
                    return f
            except Exception:
                continue
    return None


@router.post("/option-chain/recalibrate-atm")
async def recalibrate_atm(
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """
    Button 1 — Frontend ATM Reset.
    Reads the current underlying LTP from the market_data DB table and
    updates the in-memory ATM cache (atm_calculator) for each index.
    The next /options/live request will use this fresh ATM to slice the
    strike window so the frontend re-centres correctly.
    """
    from app.database import get_pool as _pool
    from app.instruments.atm_calculator import update_atm, get_atm, set_atm
    from app.market_data.greeks_poller import persist_atm

    pool = _pool()
    results = []

    for underlying in _OC_UNDERLYINGS:
        step = _OC_STRIKE_INTERVALS.get(underlying, 50)
        old_atm = get_atm(underlying)
        try:
            row = await pool.fetchrow(
                """
                WITH nearest AS (
                    SELECT MIN(expiry_date) AS exp
                    FROM option_chain_data
                    WHERE underlying = $1
                      AND expiry_date >= CURRENT_DATE
                ),
                legs AS (
                    SELECT
                        ocd.strike_price,
                        ocd.option_type,
                        COALESCE(md.ltp, ocd.prev_close) AS px
                    FROM option_chain_data ocd
                    LEFT JOIN market_data md ON md.instrument_token = ocd.instrument_token
                    JOIN nearest n ON ocd.expiry_date = n.exp
                    WHERE ocd.underlying = $1
                )
                SELECT
                    strike_price,
                    MAX(CASE WHEN option_type = 'CE' THEN px END) AS ce_px,
                    MAX(CASE WHEN option_type = 'PE' THEN px END) AS pe_px
                FROM legs
                GROUP BY strike_price
                HAVING MAX(CASE WHEN option_type = 'CE' THEN px END) IS NOT NULL
                   AND MAX(CASE WHEN option_type = 'PE' THEN px END) IS NOT NULL
                ORDER BY (MAX(CASE WHEN option_type = 'CE' THEN px END)
                       +  MAX(CASE WHEN option_type = 'PE' THEN px END)) ASC,
                         strike_price ASC
                LIMIT 1
                """,
                underlying,
            )
            if row and row["strike_price"] is not None:
                strike = float(row["strike_price"])
                new_atm = set_atm(underlying, strike)
                results.append({
                    "underlying": underlying,
                    "old_atm": float(old_atm) if old_atm is not None else None,
                    "new_atm": float(new_atm),
                    "status": "updated_from_straddle",
                })
                await persist_atm(underlying, float(new_atm))
            else:
                # Fallback to LTP-based rounding if no CE+PE pair is available.
                ltp_row = await pool.fetchrow(
                    """
                    SELECT md.ltp
                    FROM market_data md
                    JOIN instrument_master im ON im.instrument_token = md.instrument_token
                    WHERE im.symbol = $1
                      AND im.instrument_type = 'INDEX'
                    LIMIT 1
                    """,
                    underlying,
                )
                ltp = float(ltp_row["ltp"]) if (ltp_row and ltp_row["ltp"]) else None
                if ltp and ltp > 0:
                    new_atm = update_atm(underlying, ltp, step)
                    results.append({
                        "underlying": underlying,
                        "ltp": ltp,
                        "old_atm": float(old_atm) if old_atm is not None else None,
                        "new_atm": float(new_atm),
                        "status": "updated_ltp_fallback",
                    })
                    await persist_atm(underlying, float(new_atm))
                else:
                    results.append({"underlying": underlying, "status": "no_data"})
        except Exception as exc:
            results.append({"underlying": underlying, "status": "error", "error": str(exc)})

    return {
        "success": True,
        "message": "ATM cache updated from DB LTP. Next /options/live fetch will use these ATM values.",
        "results": results,
    }


@router.post("/option-chain/rebuild-skeleton")
async def rebuild_option_chain_skeleton(
    current_user: CurrentUser = Depends(get_super_admin_user),
):
    """
    Button 2 — Full Backend Rebuild.
    1. Fetches the current underlying spot price directly from DhanHQ REST API
       for each index (fresh, not from DB cache).
    2. Updates the ATM cache with the live price.
    3. Calls greeks_poller.build_skeleton() to rebuild option_chain_data rows
       centred on the fresh ATM.
    4. Triggers an immediate forced Greeks poll (force_once) to re-hydrate
       prices from the Dhan WS snapshot.
    """
    from app.instruments.atm_calculator import update_atm, get_atm, set_atm
    from app.market_data.atm_selector import legs_from_rest_optionchain, select_atm_from_straddle_legs
    from app.market_data.greeks_poller import persist_atm
    from app.market_data.index_underlyings import (
        IDX_SEG as _IDX_SEG,
        resolve_index_security_id,
        resolve_nearest_optidx_expiry,
    )

    atm_results = []
    for underlying in _OC_UNDERLYINGS:
        step = _OC_STRIKE_INTERVALS.get(underlying, 50)
        old_atm = get_atm(underlying)
        security_id = await resolve_index_security_id(underlying)
        if not security_id:
            atm_results.append({"underlying": underlying, "status": "no_security_id"})
            continue
        try:
            # Resolve nearest active expiry from instrument_master.
            # option_chain_data may lag after resets and produce stale/no expiry.
            expiry = await resolve_nearest_optidx_expiry(underlying)
            if not expiry:
                atm_results.append({"underlying": underlying, "status": "no_expiry"})
                continue
            expiry_str = str(expiry)

            # Fetch authoritative index spot first via marketfeed/ltp.
            # /optionchain last_price can drift for some underlyings (notably SENSEX).
            ltp_f: float | None = None
            try:
                ltp_resp = await dhan_client.post(
                    "/marketfeed/ltp",
                    json={_IDX_SEG: [security_id]},
                )
                if ltp_resp.status_code == 200:
                    ltp_f = _extract_ltp_from_marketfeed_payload(ltp_resp.json(), security_id)
            except Exception:
                ltp_f = None

            resp = await dhan_client.post(
                "/optionchain",
                json={
                    "UnderlyingScrip": security_id,
                    "UnderlyingSeg":   _IDX_SEG,
                    "Expiry":          expiry_str,
                },
            )
            if resp.status_code != 200:
                atm_results.append({
                    "underlying": underlying,
                    "status": f"dhan_error_{resp.status_code}",
                })
                continue

            data = resp.json().get("data", {})
            oc = data.get("oc", {})
            ltp = data.get("last_price")
            if ltp_f is None:
                try:
                    ltp_raw = float(ltp) if ltp is not None else None
                    ltp_f = ltp_raw if (ltp_raw is not None and ltp_raw > 0) else None
                except Exception:
                    ltp_f = None

            spot_for_atm = ltp_f
            if spot_for_atm is None:
                try:
                    lp_raw = float(ltp) if ltp is not None else None
                    spot_for_atm = lp_raw if (lp_raw is not None and lp_raw > 0) else None
                except Exception:
                    spot_for_atm = None

            legs = legs_from_rest_optionchain(oc)
            best_strike, atm_meta = select_atm_from_straddle_legs(
                legs,
                spot_price=spot_for_atm,
                strike_step=float(step),
            )

            if best_strike is None:
                if ltp_f is not None:
                    new_atm = update_atm(underlying, ltp_f, step)
                    atm_results.append({
                        "underlying": underlying,
                        "dhan_ltp": ltp_f,
                        "spot_source": "marketfeed_ltp_or_optionchain_last_price",
                        "old_atm": float(old_atm) if old_atm is not None else None,
                        "new_atm": float(new_atm),
                        "method": "ltp_rounding_fallback",
                        "status": "atm_updated_ltp_fallback",
                    })
                    await persist_atm(underlying, float(new_atm))
                    continue
                atm_results.append({"underlying": underlying, "status": "no_ce_pe_pair_from_dhan"})
                continue

            new_atm = set_atm(underlying, best_strike, ltp_f)
            chosen_legs = legs.get(float(best_strike), {})
            atm_results.append({
                "underlying": underlying,
                "dhan_ltp":   ltp_f,
                "spot_source": "marketfeed_ltp_or_optionchain_last_price",
                "old_atm":    float(old_atm) if old_atm is not None else None,
                "new_atm":    float(new_atm),
                "method":     atm_meta.get("method") or "straddle_min_from_optionchain_rest",
                "rounded_spot_strike": atm_meta.get("rounded_spot"),
                "atm_ce_ltp": chosen_legs.get("CE"),
                "atm_pe_ltp": chosen_legs.get("PE"),
                "status":     "atm_updated_from_straddle",
            })
            await persist_atm(underlying, float(new_atm))
        except Exception as exc:
            atm_results.append({"underlying": underlying, "status": "error", "error": str(exc)})

    # Rebuild the skeleton centred on the fresh ATMs
    skeleton_status = "ok"
    try:
        await greeks_poller.build_skeleton()
        # Trigger an immediate poll to re-hydrate prices from Dhan WS snapshot
        greeks_poller._force_once = True
    except Exception as exc:
        skeleton_status = f"skeleton_error: {exc}"

    return {
        "success": True,
        "message": "ATM updated from Dhan REST. Skeleton rebuilt. Forced Greeks poll queued.",
        "atm_updates": atm_results,
        "skeleton_rebuild": skeleton_status,
    }


    # ── All Orders View (Admin Tracking) ───────────────────────────────────────

    @router.get("/all-orders")
    async def get_all_orders(
        user_id: Optional[str] = None,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        side: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        current_user: CurrentUser = Depends(get_admin_user),
    ):
        """
        Admin-only endpoint to view ALL orders from all users.
        Includes cancelled, filled, partial, and pending orders.
    
        Query filters:
        - user_id: Filter by specific user
        - symbol: Filter by symbol name
        - status: Filter by order status (FILLED, CANCELLED, PENDING, PARTIAL, REJECTED)
        - side: Filter by BUY/SELL
        - limit: Max records to return (default 100, max 500)
        - offset: Pagination offset
    
        Returns full order details for tracking discrepancies.
        """
        from app.database import get_pool
    
        pool = get_pool()
        if pool is None:
            raise HTTPException(status_code=500, detail="Database pool not initialized")
    
        # Build WHERE clause dynamically
        where_clauses = []
        params = []
        param_index = 1
    
        if user_id:
            where_clauses.append(f"po.user_id = ${param_index}")
            params.append(user_id)
            param_index += 1
    
        if symbol:
            where_clauses.append(f"UPPER(po.symbol) LIKE ${param_index}")
            params.append(f"%{symbol.upper()}%")
            param_index += 1
    
        if status:
            where_clauses.append(f"po.status = ${param_index}")
            params.append(status.upper())
            param_index += 1
    
        if side:
            where_clauses.append(f"po.side = ${param_index}")
            params.append(side.upper())
            param_index += 1
    
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    
        # Limit validation
        limit = min(max(1, limit), 500)
        params.append(limit)
        limit_param = f"${param_index}"
        param_index += 1
    
        params.append(offset)
        offset_param = f"${param_index}"
    
        # Get total count
        count_query = f"""
            SELECT COUNT(*) as total
            FROM paper_orders po
            WHERE {where_sql}
        """
        count_row = await pool.fetchrow(count_query, *params[:-2])  # Exclude limit/offset
        total_count = count_row["total"] if count_row else 0
    
        # Get orders with user details
        query = f"""
            SELECT 
                po.order_id,
                po.user_id,
                pa.email as user_email,
                pa.full_name as user_name,
                pa.role as user_role,
                po.instrument_token,
                po.symbol,
                po.exchange_segment,
                po.side,
                po.order_type,
                po.quantity,
                po.filled_qty,
                po.remaining_qty,
                po.price,
                po.trigger_price,
                po.status,
                po.avg_fill_price,
                po.product_type,
                po.validity,
                po.rejection_reason,
                po.source,
                po.created_at,
                po.updated_at,
                COALESCE(
                    (SELECT COUNT(*) FROM paper_trades pt WHERE pt.order_id = po.order_id),
                    0
                ) as trade_count,
                COALESCE(
                    (SELECT SUM(COALESCE(fill_qty, quantity)) FROM paper_trades pt WHERE pt.order_id = po.order_id),
                    0
                ) as total_filled_from_trades
            FROM paper_orders po
            LEFT JOIN paper_accounts pa ON po.user_id = pa.user_id
            WHERE {where_sql}
            ORDER BY po.created_at DESC
            LIMIT {limit_param}
            OFFSET {offset_param}
        """
    
        rows = await pool.fetch(query, *params)
    
        orders = []
        for row in rows:
            orders.append({
                "order_id": str(row["order_id"]),
                "user_id": str(row["user_id"]),
                "user_email": row["user_email"],
                "user_name": row["user_name"],
                "user_role": row["user_role"],
                "instrument_token": row["instrument_token"],
                "symbol": row["symbol"],
                "exchange_segment": row["exchange_segment"],
                "side": row["side"],
                "order_type": row["order_type"],
                "quantity": row["quantity"],
                "filled_qty": row["filled_qty"],
                "remaining_qty": row["remaining_qty"],
                "price": float(row["price"]) if row["price"] else None,
                "trigger_price": float(row["trigger_price"]) if row["trigger_price"] else None,
                "status": row["status"],
                "avg_fill_price": float(row["avg_fill_price"]) if row["avg_fill_price"] else None,
                "product_type": row["product_type"],
                "validity": row["validity"],
                "rejection_reason": row["rejection_reason"],
                "source": row["source"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                "trade_count": row["trade_count"],
                "total_filled_from_trades": row["total_filled_from_trades"],
            })
    
        return {
            "success": True,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "orders": orders,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Activity Audit Log endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/admin/activity-logs")
async def get_activity_logs(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    role: Optional[str] = None,
    action_type: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    subject_user_id: Optional[str] = None,
    ip: Optional[str] = None,
    search: Optional[str] = None,
    include_super_admin: bool = False,
    limit: int = 50,
    offset: int = 0,
    _: CurrentUser = Depends(get_admin_user),
):
    """
    List activity audit log entries.
    SUPER_ADMIN rows are excluded by default (include_super_admin=False).
    Supports filtering by date range, role, action type, user, IP and text search.
    """
    from app.database import get_pool
    pool = get_pool()
    safe_limit = max(1, min(limit, 200))
    safe_offset = max(0, offset)

    conditions = []
    args = []

    def _add(cond: str, val):
        args.append(val)
        conditions.append(cond.replace("?", f"${len(args)}"))

    if not include_super_admin:
        conditions.append("(a.actor_role IS NULL OR a.actor_role NOT IN ('SUPER_ADMIN'))")

    if from_date:
        _add("a.created_at >= ?::timestamptz", from_date)
    if to_date:
        _add("a.created_at <= ?::timestamptz", to_date)
    if role:
        _add("a.actor_role = ?", role.upper())
    if action_type:
        _add("a.action_type = ?", action_type.upper())
    if actor_user_id:
        _add("a.actor_user_id = ?::uuid", actor_user_id)
    if subject_user_id:
        _add("a.subject_user_id = ?::uuid", subject_user_id)
    if ip:
        _add("a.ip_address ILIKE ?", f"%{ip}%")
    if search:
        base_idx = len(args) + 1
        for _ in range(5):
            args.append(f"%{search}%")
        conditions.append(
            f"(a.actor_name ILIKE ${base_idx} OR a.subject_name ILIKE ${base_idx + 1}"
            f" OR a.action_type ILIKE ${base_idx + 2} OR a.ip_address ILIKE ${base_idx + 3}"
            f" OR a.geo_city ILIKE ${base_idx + 4})"
        )

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM activity_audit_log a {where}",
        *args,
    )

    args_page = args + [safe_limit, safe_offset]
    rows = await pool.fetch(
        f"""
        SELECT a.id, a.actor_user_id, a.actor_name, a.actor_role,
               a.subject_user_id, a.subject_name,
               a.action_type, a.resource_type, a.resource_id,
               a.endpoint, a.http_method, a.status_code, a.error_detail,
               a.ip_address, a.user_agent,
               a.geo_country, a.geo_country_code, a.geo_region, a.geo_city,
               a.geo_latitude, a.geo_longitude,
               a.metadata, a.created_at
        FROM activity_audit_log a
        {where}
        ORDER BY a.created_at DESC
        LIMIT ${len(args_page) - 1} OFFSET ${len(args_page)}
        """,
        *args_page,
    )

    def _row(r):
        return {
            "id": r["id"],
            "actor_user_id": str(r["actor_user_id"]) if r["actor_user_id"] else None,
            "actor_name": r["actor_name"],
            "actor_role": r["actor_role"],
            "subject_user_id": str(r["subject_user_id"]) if r["subject_user_id"] else None,
            "subject_name": r["subject_name"],
            "action_type": r["action_type"],
            "resource_type": r["resource_type"],
            "resource_id": r["resource_id"],
            "endpoint": r["endpoint"],
            "http_method": r["http_method"],
            "status_code": r["status_code"],
            "error_detail": r["error_detail"],
            "ip_address": r["ip_address"],
            "user_agent": r["user_agent"],
            "geo_country": r["geo_country"],
            "geo_country_code": r["geo_country_code"],
            "geo_region": r["geo_region"],
            "geo_city": r["geo_city"],
            "geo_latitude": float(r["geo_latitude"]) if r["geo_latitude"] is not None else None,
            "geo_longitude": float(r["geo_longitude"]) if r["geo_longitude"] is not None else None,
            "metadata": r["metadata"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }

    return {
        "success": True,
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "items": [_row(r) for r in rows],
    }


@router.get("/admin/activity-logs/export")
async def export_activity_logs(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    role: Optional[str] = None,
    action_type: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    ip: Optional[str] = None,
    search: Optional[str] = None,
    include_super_admin: bool = False,
    _: CurrentUser = Depends(get_admin_user),
):
    """Export activity log as CSV (max 10 000 rows)."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    pool = get_pool()
    conditions = []
    args = []

    def _add(cond: str, val):
        args.append(val)
        conditions.append(cond.replace("?", f"${len(args)}"))

    if not include_super_admin:
        conditions.append("(a.actor_role IS NULL OR a.actor_role NOT IN ('SUPER_ADMIN'))")

    if from_date:
        _add("a.created_at >= ?::timestamptz", from_date)
    if to_date:
        _add("a.created_at <= ?::timestamptz", to_date)
    if role:
        _add("a.actor_role = ?", role.upper())
    if action_type:
        _add("a.action_type = ?", action_type.upper())
    if actor_user_id:
        _add("a.actor_user_id = ?::uuid", actor_user_id)
    if ip:
        _add("a.ip_address ILIKE ?", f"%{ip}%")
    if search:
        base_idx = len(args) + 1
        for _ in range(5):
            args.append(f"%{search}%")
        conditions.append(
            f"(a.actor_name ILIKE ${base_idx} OR a.subject_name ILIKE ${base_idx + 1}"
            f" OR a.action_type ILIKE ${base_idx + 2} OR a.ip_address ILIKE ${base_idx + 3}"
            f" OR a.geo_city ILIKE ${base_idx + 4})"
        )

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    args_export = args + [10000]

    rows = await pool.fetch(
        f"""
        SELECT a.id, a.actor_name, a.actor_role, a.subject_name,
               a.action_type, a.resource_type, a.resource_id,
               a.endpoint, a.http_method, a.status_code,
               a.ip_address, a.user_agent,
               a.geo_country, a.geo_region, a.geo_city,
               a.geo_latitude, a.geo_longitude,
               a.error_detail, a.created_at
        FROM activity_audit_log a
        {where}
        ORDER BY a.created_at DESC
        LIMIT ${len(args_export)}
        """,
        *args_export,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Actor Name", "Actor Role", "Subject Name",
        "Action Type", "Resource Type", "Resource ID",
        "Endpoint", "Method", "Status Code",
        "IP Address", "User Agent",
        "Country", "Region", "City", "Latitude", "Longitude",
        "Error Detail", "Created At",
    ])
    for r in rows:
        writer.writerow([
            r["id"], r["actor_name"], r["actor_role"], r["subject_name"],
            r["action_type"], r["resource_type"], r["resource_id"],
            r["endpoint"], r["http_method"], r["status_code"],
            r["ip_address"], r["user_agent"],
            r["geo_country"] or "", r["geo_region"] or "", r["geo_city"] or "",
            r["geo_latitude"] or "", r["geo_longitude"] or "",
            r["error_detail"],
            r["created_at"].isoformat() if r["created_at"] else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=activity_logs.csv"},
    )
