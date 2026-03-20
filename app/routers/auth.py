"""Authentication, portal signup, OTP, and password reset endpoints."""
import logging
from typing import Optional, List
import re
import base64
import hashlib
import json
import secrets

import bcrypt
import httpx
from fastapi  import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.database      import get_pool
from app.dependencies  import CurrentUser, get_current_user, get_admin_user
from app.config import get_settings
from app.runtime.audit_logger import log_activity
from app.runtime.security_alerts import check_burst, check_impossible_travel, check_otp_failures

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Auth"])

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


def _effective_admin_permissions(role: str, raw_permissions) -> List[str]:
    if role != "ADMIN":
        return list(raw_permissions or [])
    if raw_permissions is None:
        return list(ADMIN_TAB_PERMISSION_KEYS)
    return [p for p in list(raw_permissions or []) if p in ADMIN_TAB_PERMISSION_KEYS]


def _hash(pw: str) -> str:
    """Hash a plain-text password with bcrypt. Returns a UTF-8 string."""
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _check(pw: str, stored_hash: str) -> bool:
    """Verify a plain-text password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(pw.encode(), stored_hash.encode())
    except Exception:
        return False


class LoginRequest(BaseModel):
    mobile:   str
    password: str


class LogoutRequest(BaseModel):
    token: Optional[str] = None


class PortalSignupRequest(BaseModel):
    name: str
    email: str
    mobile: str
    city: str
    experience_level: str
    interest: str
    learning_goal: str


class PortalUsersBulkDeleteRequest(BaseModel):
    user_ids: List[str]


class SendPhoneOtpRequest(BaseModel):
    phone: str
    purpose: str = "signup"


class VerifyPhoneOtpRequest(BaseModel):
    phone: str
    purpose: str = "signup"
    otp: str


class SendEmailOtpRequest(BaseModel):
    email: str
    purpose: str = "signup"


class VerifyEmailOtpRequest(BaseModel):
    email: str
    purpose: str = "signup"
    otp: str


class AccountSignupRequest(BaseModel):
    first_name: str
    middle_name: Optional[str] = ""
    last_name: str
    phone: str
    email: str
    address: Optional[str] = ""
    city: Optional[str] = ""
    state: Optional[str] = ""
    country: Optional[str] = "India"
    pan_number: str
    aadhar_number: str
    pan_upload: str
    aadhar_upload: str
    bank_account_number: str
    ifsc: str
    upi_id: Optional[str] = ""


class PortalSignupReviewRequest(BaseModel):
    action: str
    reason: Optional[str] = ""


class ForgotPasswordSendOtpRequest(BaseModel):
    mobile: str


class ForgotPasswordResetRequest(BaseModel):
    mobile: str
    otp: str
    new_password: str


_OTP_PURPOSE_SIGNUP = "signup"
_OTP_PURPOSE_PASSWORD_RESET = "password_reset"
_OTP_PURPOSES = {_OTP_PURPOSE_SIGNUP, _OTP_PURPOSE_PASSWORD_RESET}

_SMS_OTP_DEFAULTS = {
    "message_central_customer_id": "C-44071166CC38423",
    "message_central_password": "Allalone@01",
    "otp_expiry_seconds": 180,
    "otp_resend_cooldown_seconds": 300,
    "otp_max_attempts": 5,
}


def _to_positive_int(value, fallback: int) -> int:
    try:
        parsed = int(str(value).strip())
        return parsed if parsed > 0 else fallback
    except Exception:
        return fallback


async def _get_sms_otp_runtime_settings() -> dict:
    """Load SMS OTP settings from system_config with safe defaults."""
    pool = get_pool()
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


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _serialize_signup_review_log(row) -> dict:
    return {
        "id": int(row["id"]),
        "signup_id": str(row["signup_id"]),
        "signup_name": row.get("signup_name") or "",
        "signup_email": row.get("signup_email") or "",
        "signup_mobile": row.get("signup_mobile") or "",
        "action": row["action"],
        "previous_status": row["previous_status"],
        "new_status": row["new_status"],
        "reason": row.get("reason") or "",
        "actor_name": row.get("actor_name") or "",
        "actor_mobile": row.get("actor_mobile") or "",
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


async def _insert_signup_review_log(
    conn,
    *,
    signup_id: str,
    action: str,
    previous_status: str,
    new_status: str,
    reason: str,
    actor: CurrentUser,
) -> None:
    await conn.execute(
        """
        INSERT INTO user_signup_review_log (
            signup_id,
            action,
            previous_status,
            new_status,
            reason,
            actor_user_id,
            actor_name,
            actor_mobile
        )
        VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid, $7, $8)
        """,
        signup_id,
        action,
        previous_status,
        new_status,
        reason,
        actor.id,
        (actor.name or "").strip(),
        (actor.mobile or "").strip(),
    )


def _normalize_mobile(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) != 10:
        raise HTTPException(status_code=400, detail="Enter a valid 10-digit mobile number")
    return digits


def _otp_hash(otp: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{otp}".encode("utf-8")).hexdigest()


def _validate_image_payload(data_url: str, label: str) -> None:
    value = (data_url or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    if not value.startswith("data:image/"):
        raise HTTPException(status_code=400, detail=f"{label} must be an image")
    if "," not in value:
        raise HTTPException(status_code=400, detail=f"{label} is invalid")
    try:
        payload = value.split(",", 1)[1]
        size = len(base64.b64decode(payload, validate=True))
    except Exception:
        raise HTTPException(status_code=400, detail=f"{label} is invalid")
    if size > 1_048_576:
        raise HTTPException(status_code=400, detail=f"{label} must be 1 MB or smaller")


async def _mc_get_token() -> str:
    """Fetch a fresh Message Central auth token."""
    cfg = get_settings()
    runtime = await _get_sms_otp_runtime_settings()
    customer_id = runtime["message_central_customer_id"]
    password = runtime["message_central_password"]
    if not customer_id or not password:
        if cfg.debug:
            return "debug_token"
        raise HTTPException(status_code=503, detail="SMS service is not configured")
    encoded_password = base64.b64encode(password.encode()).decode()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://cpaas.messagecentral.com/auth/v1/authentication/token",
            params={"customerId": customer_id, "key": encoded_password, "scope": "NEW", "country": "91"},
            headers={"accept": "*/*"},
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail="SMS service authentication failed")
    try:
        data = resp.json()
        token = (
            data.get("data", {}).get("authToken")
            or data.get("authToken")
            or data.get("token")
        )
        if not token:
            raise ValueError("missing token")
        return str(token)
    except Exception:
        raise HTTPException(status_code=502, detail="SMS service authentication failed")


async def _mc_send_otp(phone: str) -> tuple[str, dict]:
    """Send OTP via Message Central. Returns (verificationId, raw_response)."""
    cfg = get_settings()
    runtime = await _get_sms_otp_runtime_settings()
    customer_id = runtime["message_central_customer_id"]
    if not customer_id:
        if cfg.debug:
            return "debug_verification_id", {"provider": "debug", "status": "skipped"}
        raise HTTPException(status_code=503, detail="SMS service is not configured")
    token = await _mc_get_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://cpaas.messagecentral.com/verification/v3/send",
            params={
                "countryCode": "91",
                "customerId": customer_id,
                "flowType": "SMS",
                "mobileNumber": phone,
                "otpLength": "6",
            },
            headers={"authToken": token},
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail="Failed to send OTP right now")
    try:
        data = resp.json()
        verification_id = str(data["data"]["verificationId"])
        return verification_id, data
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to send OTP right now")


async def _mc_validate_otp(verification_id: str, code: str) -> bool:
    """Validate OTP with Message Central. Returns True on success."""
    if verification_id == "debug_verification_id":
        return code == "123456"
    runtime = await _get_sms_otp_runtime_settings()
    customer_id = runtime["message_central_customer_id"]
    token = await _mc_get_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            "https://cpaas.messagecentral.com/verification/v3/validateOtp",
            params={"verificationId": verification_id, "code": code, "flowType": "SMS", "customerId": customer_id},
            headers={"authToken": token},
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail="Failed to validate OTP")
    try:
        data = resp.json()
        rc = str(data.get("responseCode") or data.get("data", {}).get("responseCode") or "")
        status = data.get("verificationStatus") or data.get("data", {}).get("verificationStatus", "")
        if rc == "705":
            raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")
        if rc == "703":
            raise HTTPException(status_code=400, detail="OTP already verified.")
        if rc in {"702", "701", "706"}:
            return False
        return status == "VERIFICATION_COMPLETED"
    except HTTPException:
        raise
    except Exception:
        return False


async def _send_phone_otp(phone_raw: str, purpose_raw: str, request: Request) -> dict:
    purpose = (purpose_raw or "").strip().lower()
    if purpose not in _OTP_PURPOSES:
        raise HTTPException(status_code=400, detail="Invalid OTP purpose")

    phone = _normalize_mobile(phone_raw)
    pool = get_pool()
    runtime = await _get_sms_otp_runtime_settings()

    if purpose == _OTP_PURPOSE_SIGNUP:
        exists_user = await pool.fetchval(
            "SELECT 1 FROM users WHERE mobile=$1",
            phone,
        )
        if exists_user:
            raise HTTPException(status_code=409, detail="number/email already registered")

        exists_signup = await pool.fetchval(
                "SELECT 1 FROM user_signups WHERE mobile=$1",
            phone,
        )
        if exists_signup:
            raise HTTPException(status_code=409, detail="number/email already registered")
    else:
        reset_user = await pool.fetchrow(
            "SELECT id, role, is_active FROM users WHERE mobile=$1",
            phone,
        )
        if not reset_user:
            raise HTTPException(status_code=404, detail="User not found")
        if reset_user["role"] != "USER":
            raise HTTPException(status_code=403, detail="Password reset OTP is allowed only for users")
        if not reset_user["is_active"]:
            raise HTTPException(status_code=403, detail="Account is inactive")

    recent = await pool.fetchval(
        """
        SELECT 1
        FROM otp_verifications
        WHERE contact_type='PHONE'
          AND contact_value=$1
          AND purpose=$2
          AND created_at > NOW() - (($3::text || ' seconds')::interval)
        LIMIT 1
        """,
        phone,
        purpose,
        str(int(runtime["otp_resend_cooldown_seconds"])),
    )
    if recent:
        raise HTTPException(status_code=429, detail="Please wait before requesting another OTP")

    # Message Central generates and delivers the OTP; we store the verificationId
    verification_id, provider_response = await _mc_send_otp(phone)

    await pool.execute(
        """
        INSERT INTO otp_verifications
            (contact_type, contact_value, purpose, otp_hash, otp_salt, expires_at, request_ip, provider_response)
        VALUES
            ('PHONE', $1, $2, 'MESSAGECENTRAL', $3, NOW() + (($4::text || ' seconds')::interval), $5, $6::jsonb)
        """,
        phone,
        purpose,
        verification_id,
        str(int(runtime["otp_expiry_seconds"])),
        request.client.host if request.client else None,
        json.dumps(provider_response),
    )

    return {
        "success": True,
        "message": "OTP sent successfully",
        "expires_in_seconds": int(runtime["otp_expiry_seconds"]),
    }


def _normalize_otp_service_base_url(raw: str) -> str:
    return (raw or "").strip().rstrip("/")


async def _email_otp_generate(email: str, cfg) -> dict:
    base_url = _normalize_otp_service_base_url(cfg.email_otp_service_base_url)
    if not base_url:
        raise HTTPException(status_code=503, detail="Email OTP service is not configured")
    payload = {
        "email": email,
        "type": "numeric",
        "organization": "Straddly",
        "subject": "Email OTP Verification",
    }
    timeout_seconds = float(max(1, int(cfg.email_otp_service_timeout_seconds)))
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(f"{base_url}/api/otp/generate", json=payload)
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach email OTP service")

    if resp.status_code >= 400:
        err = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        detail = err.get("error") or err.get("detail") or "Could not send email OTP"
        raise HTTPException(status_code=400 if resp.status_code < 500 else 502, detail=detail)

    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return data if isinstance(data, dict) else {"message": "Email OTP sent"}


async def _email_otp_verify(email: str, otp: str, cfg) -> dict:
    base_url = _normalize_otp_service_base_url(cfg.email_otp_service_base_url)
    if not base_url:
        raise HTTPException(status_code=503, detail="Email OTP service is not configured")
    timeout_seconds = float(max(1, int(cfg.email_otp_service_timeout_seconds)))
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(f"{base_url}/api/otp/verify", json={"email": email, "otp": otp})
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach email OTP service")

    if resp.status_code >= 400:
        err = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        detail = err.get("error") or err.get("detail") or "Invalid email OTP"
        raise HTTPException(status_code=400 if resp.status_code < 500 else 502, detail=detail)

    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return data if isinstance(data, dict) else {"message": "OTP verified"}


async def _send_email_otp(email_raw: str, purpose_raw: str, request: Request) -> dict:
    purpose = (purpose_raw or "").strip().lower()
    if purpose not in _OTP_PURPOSES:
        raise HTTPException(status_code=400, detail="Invalid OTP purpose")

    email = _normalize_email(email_raw)
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    pool = get_pool()
    cfg = get_settings()

    if purpose == _OTP_PURPOSE_SIGNUP:
        exists_user = await pool.fetchval(
            "SELECT 1 FROM users WHERE lower(email)=lower($1)",
            email,
        )
        if exists_user:
            raise HTTPException(status_code=409, detail="number/email already registered")

        exists_signup = await pool.fetchval(
            "SELECT 1 FROM user_signups WHERE lower(email)=lower($1)",
            email,
        )
        if exists_signup:
            raise HTTPException(status_code=409, detail="number/email already registered")

    recent = await pool.fetchval(
        """
        SELECT 1
        FROM otp_verifications
        WHERE contact_type='EMAIL'
          AND contact_value=$1
          AND purpose=$2
          AND created_at > NOW() - (($3::text || ' seconds')::interval)
        LIMIT 1
        """,
        email,
        purpose,
        str(int(cfg.otp_resend_cooldown_seconds)),
    )
    if recent:
        raise HTTPException(status_code=429, detail="Please wait before requesting another OTP")

    provider_response = await _email_otp_generate(email, cfg)

    await pool.execute(
        """
        INSERT INTO otp_verifications
            (contact_type, contact_value, purpose, otp_hash, otp_salt, expires_at, request_ip, provider_response)
        VALUES
            ('EMAIL', $1, $2, 'EMAIL_SERVICE', 'EMAIL_SERVICE', NOW() + (($3::text || ' seconds')::interval), $4, $5::jsonb)
        """,
        email,
        purpose,
        str(int(cfg.email_otp_expiry_seconds)),
        request.client.host if request.client else None,
        json.dumps(provider_response),
    )

    return {
        "success": True,
        "message": provider_response.get("message") or "OTP is generated and sent to your email",
        "expires_in_seconds": int(cfg.email_otp_expiry_seconds),
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request):
    pool = get_pool()
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else ""))
    ua = request.headers.get("user-agent", "")

    user = await pool.fetchrow(
        "SELECT * FROM users WHERE mobile=$1 AND is_active=true", body.mobile
    )
    if not user:
        log_activity(action_type="LOGIN_FAILED", request=request,
                     status_code=401, error_detail="User not found",
                     metadata={"mobile": body.mobile})
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Check if user is archived (soft deleted)
    if user.get("is_archived"):
        log_activity(action_type="LOGIN_FAILED", request=request,
                     actor_user_id=str(user["id"]), actor_name=user["name"],
                     actor_role=user["role"], status_code=403,
                     error_detail="Account archived")
        raise HTTPException(status_code=403, detail="Account has been archived and is unavailable")

    if not _check(body.password, user["password_hash"]):
        log_activity(action_type="LOGIN_FAILED", request=request,
                     actor_user_id=str(user["id"]), actor_name=user["name"],
                     actor_role=user["role"], status_code=401,
                     error_detail="Wrong password")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    session = await pool.fetchrow(
        """
        INSERT INTO user_sessions (user_id, expires_at, ip_address, user_agent)
        VALUES ($1, NOW() + INTERVAL '12 hours', $2, $3)
        RETURNING token
        """,
        user["id"],
        ip or None,
        ua or None,
    )
    access_token = str(session["token"])

    log_activity(action_type="LOGIN", request=request,
                 actor_user_id=str(user["id"]), actor_name=user["name"],
                 actor_role=user["role"], status_code=200)
    check_burst(ip, str(user["id"]))
    check_impossible_travel(str(user["id"]), ip)

    return {
        "access_token": access_token,
        "token_type":   "bearer",
        "user": {
            "id":     str(user["id"]),
            "name":   user["name"],
            "mobile": user["mobile"],
            "role":   user["role"],
            "permissions": _effective_admin_permissions(user["role"], user.get("admin_tab_permissions")),
        },
    }


@router.post("/logout")
async def logout(body: LogoutRequest, request: Request):
    token = body.token or request.headers.get("X-AUTH")
    if not token:
        return {"success": True}
    pool = get_pool()
    # Fetch user info before deleting so we can log it
    session_row = await pool.fetchrow(
        "SELECT u.id, u.name, u.role FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.token=$1::uuid",
        token,
    )
    await pool.execute("DELETE FROM user_sessions WHERE token=$1::uuid", token)
    if session_row:
        log_activity(action_type="LOGOUT", request=request,
                     actor_user_id=str(session_row["id"]),
                     actor_name=session_row["name"],
                     actor_role=session_row["role"],
                     status_code=200)
    return {"success": True}


@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    return {
        "id":     user.id,
        "name":   user.name,
        "mobile": user.mobile,
        "role":   user.role,
        "permissions": _effective_admin_permissions(user.role, user.permissions),
    }


@router.post("/portal/signup")
async def portal_signup(body: PortalSignupRequest, request: Request):
    """
    Register a new user for the educational portal.
    
    Validates:
    - Email format
    - Required fields (name, email, experience_level)
    - Email uniqueness
    
    Returns:
    - user_id: UUID of the created portal user
    - message: Confirmation message
    """
    pool = get_pool()
    
    # Validate email format
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(email_pattern, body.email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    # Validate required fields
    if not body.name or not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    
    if not body.experience_level or not body.experience_level.strip():
        raise HTTPException(status_code=400, detail="Experience level is required")
    
    # Normalize inputs
    email = body.email.strip().lower()
    name = body.name.strip()
    experience_level = body.experience_level.strip()
    
    try:
        # Check if email already exists
        existing = await pool.fetchrow(
            "SELECT id FROM portal_users WHERE email=$1",
            email
        )
        
        if existing:
            raise HTTPException(
                status_code=409,
                detail="Email already registered for the educational portal"
            )
        
        # Insert new portal user
        from app.runtime.geoip import lookup as _geo
        _ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
               or (request.client.host if request.client else ""))
        _ua = request.headers.get("user-agent", "")
        _geo_r = _geo(_ip)
        user = await pool.fetchrow(
            """
            INSERT INTO portal_users
                (name, email, mobile, city, experience_level, interest, learning_goal,
                 ip_details, user_agent, geo_country, geo_region, geo_city,
                 sms_verified, email_verified)
            VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, FALSE, FALSE)
            RETURNING id, name, email, mobile, city, experience_level, interest, learning_goal, created_at
            """,
            name,
            email,
            body.mobile,
            body.city,
            experience_level,
            body.interest,
            body.learning_goal,
            _ip,
            _ua,
            _geo_r.country,
            _geo_r.region,
            _geo_r.city,
        )
        
        log.info(f"New portal signup: {email}")
        log_activity(action_type="ENROLLMENT_SUBMIT", request=request,
                     resource_type="portal_user", resource_id=str(user["id"]),
                     status_code=200, metadata={"email": email, "name": name})
        check_burst(_ip)
        
        return {
            "success": True,
            "message": f"Welcome {name}! You've been registered for the Straddly educational portal.",
            "user_id": str(user["id"]),
            "email": user["email"],
        }
    
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Portal signup error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create account. Please try again later.")


@router.post("/otp/send-phone")
async def send_phone_otp(body: SendPhoneOtpRequest, request: Request):
    return await _send_phone_otp(body.phone, body.purpose, request)


@router.post("/otp/send-email")
async def send_email_otp(body: SendEmailOtpRequest, request: Request):
    return await _send_email_otp(body.email, body.purpose, request)


@router.post("/otp/verify-phone")
async def verify_phone_otp(body: VerifyPhoneOtpRequest, request: Request):
    purpose = (body.purpose or "").strip().lower()
    if purpose not in _OTP_PURPOSES:
        raise HTTPException(status_code=400, detail="Invalid OTP purpose")

    phone = _normalize_mobile(body.phone)
    otp = (body.otp or "").strip()
    if not re.fullmatch(r"\d{6}", otp):
        raise HTTPException(status_code=400, detail="Enter a valid 6-digit OTP")

    pool = get_pool()
    runtime = await _get_sms_otp_runtime_settings()
    row = await pool.fetchrow(
        """
        SELECT id, otp_hash, otp_salt, attempt_count
        FROM otp_verifications
        WHERE contact_type='PHONE'
          AND contact_value=$1
          AND purpose=$2
          AND consumed_at IS NULL
          AND expires_at > NOW()
        ORDER BY created_at DESC
        LIMIT 1
        """,
        phone,
        purpose,
    )
    if not row:
        raise HTTPException(status_code=400, detail="OTP expired or not found")

    if int(row["attempt_count"] or 0) >= int(runtime["otp_max_attempts"]):
        raise HTTPException(status_code=429, detail="Too many invalid attempts")

    # Validate OTP via Message Central using the stored verificationId
    verification_id = row["otp_salt"]
    valid = await _mc_validate_otp(verification_id, otp)

    if not valid:
        await pool.execute(
            "UPDATE otp_verifications SET attempt_count = attempt_count + 1 WHERE id=$1",
            row["id"],
        )
        _otp_ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                   or (request.client.host if request.client else ""))
        log_activity(action_type="OTP_VERIFY_FAILED", request=request,
                     status_code=400, metadata={"contact_value": phone, "purpose": purpose})
        check_otp_failures(phone, _otp_ip)
        raise HTTPException(status_code=400, detail="Invalid OTP")

    await pool.execute(
        "UPDATE otp_verifications SET verified_at = NOW() WHERE id=$1",
        row["id"],
    )
    log_activity(action_type="OTP_VERIFIED", request=request,
                 status_code=200, metadata={"contact_value": phone, "purpose": purpose})
    return {"success": True, "verified": True}


@router.post("/otp/verify-email")
async def verify_email_otp(body: VerifyEmailOtpRequest, request: Request):
    purpose = (body.purpose or "").strip().lower()
    if purpose not in _OTP_PURPOSES:
        raise HTTPException(status_code=400, detail="Invalid OTP purpose")

    email = _normalize_email(body.email)
    otp = (body.otp or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    if not re.fullmatch(r"\d{6}", otp):
        raise HTTPException(status_code=400, detail="Enter a valid 6-digit OTP")

    pool = get_pool()
    cfg = get_settings()
    row = await pool.fetchrow(
        """
        SELECT id, attempt_count
        FROM otp_verifications
        WHERE contact_type='EMAIL'
          AND contact_value=$1
          AND purpose=$2
          AND consumed_at IS NULL
          AND expires_at > NOW()
        ORDER BY created_at DESC
        LIMIT 1
        """,
        email,
        purpose,
    )
    if not row:
        raise HTTPException(status_code=400, detail="OTP expired or not found")

    if int(row["attempt_count"] or 0) >= int(cfg.otp_max_attempts):
        raise HTTPException(status_code=429, detail="Too many invalid attempts")

    try:
        await _email_otp_verify(email, otp, cfg)
    except HTTPException:
        await pool.execute(
            "UPDATE otp_verifications SET attempt_count = attempt_count + 1 WHERE id=$1",
            row["id"],
        )
        _email_otp_ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                         or (request.client.host if request.client else ""))
        log_activity(action_type="OTP_VERIFY_FAILED", request=request,
                     status_code=400, metadata={"contact_value": email, "purpose": purpose})
        check_otp_failures(email, _email_otp_ip)
        raise

    await pool.execute(
        "UPDATE otp_verifications SET verified_at = NOW() WHERE id=$1",
        row["id"],
    )
    log_activity(action_type="OTP_VERIFIED", request=request,
                 status_code=200, metadata={"contact_value": email, "purpose": purpose})
    return {"success": True, "verified": True}


@router.post("/portal/account-signup")
async def account_signup(body: AccountSignupRequest, request: Request):
    pool = get_pool()
    email = _normalize_email(body.email)
    phone = _normalize_mobile(body.phone)

    if not body.first_name.strip():
        raise HTTPException(status_code=400, detail="First Name is required")
    if not body.last_name.strip():
        raise HTTPException(status_code=400, detail="Last Name is required")
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    for label, value in [
        ("PAN Number", body.pan_number),
        ("Aadhar Number", body.aadhar_number),
        ("Bank A/c Number", body.bank_account_number),
        ("IFSC", body.ifsc),
    ]:
        if not (value or "").strip():
            raise HTTPException(status_code=400, detail=f"{label} is required")

    _validate_image_payload(body.pan_upload, "PAN Upload")
    _validate_image_payload(body.aadhar_upload, "Aadhar Upload")

    exists_user = await pool.fetchval(
        "SELECT 1 FROM users WHERE mobile=$1 OR lower(email)=lower($2)",
        phone,
        email,
    )
    if exists_user:
        raise HTTPException(status_code=409, detail="number/email already registered")

    exists_signup = await pool.fetchval(
        """
        SELECT 1
        FROM user_signups
        WHERE mobile=$1 OR lower(email)=lower($2)
        """,
        phone,
        email,
    )
    if exists_signup:
        raise HTTPException(status_code=409, detail="number/email already registered")

    otp_row = await pool.fetchrow(
        """
        SELECT id
        FROM otp_verifications
        WHERE contact_type='PHONE'
          AND contact_value=$1
          AND purpose=$2
          AND verified_at IS NOT NULL
          AND consumed_at IS NULL
          AND verified_at > NOW() - INTERVAL '30 minutes'
        ORDER BY verified_at DESC
        LIMIT 1
        """,
        phone,
        _OTP_PURPOSE_SIGNUP,
    )
    if not otp_row:
        raise HTTPException(status_code=400, detail="Phone OTP verification is required")

    email_otp_row = await pool.fetchrow(
        """
        SELECT id
        FROM otp_verifications
        WHERE contact_type='EMAIL'
          AND contact_value=$1
          AND purpose=$2
          AND verified_at IS NOT NULL
          AND consumed_at IS NULL
          AND verified_at > NOW() - INTERVAL '30 minutes'
        ORDER BY verified_at DESC
        LIMIT 1
        """,
        email,
        _OTP_PURPOSE_SIGNUP,
    )
    if not email_otp_row:
        raise HTTPException(status_code=400, detail="Email OTP verification is required")

    from app.runtime.geoip import lookup as _geo
    _signup_ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                  or (request.client.host if request.client else ""))
    _signup_ua = request.headers.get("user-agent", "")
    _signup_geo = _geo(_signup_ip)

    signup = await pool.fetchrow(
        """
        INSERT INTO user_signups
            (name, first_name, middle_name, last_name, email, mobile,
             address, city, state, country,
             pan_number, aadhar_number, pan_upload, aadhar_upload,
             bank_account_number, ifsc, upi_id,
             ip_details, user_agent, geo_country, geo_region, geo_city,
             sms_verified, email_verified, status)
        VALUES
            ($1, $2, $3, $4, $5, $6,
             $7, $8, $9, $10,
             $11, $12, $13, $14,
             $15, $16, $17,
             $18, $19, $20, $21, $22,
             TRUE, TRUE, 'PENDING')
        RETURNING id
        """,
        " ".join([x for x in [body.first_name.strip(), (body.middle_name or "").strip(), body.last_name.strip()] if x]),
        body.first_name.strip(),
        (body.middle_name or "").strip(),
        body.last_name.strip(),
        email,
        phone,
        (body.address or "").strip(),
        (body.city or "").strip(),
        (body.state or "").strip(),
        (body.country or "India").strip() or "India",
        body.pan_number.strip().upper(),
        body.aadhar_number.strip(),
        body.pan_upload,
        body.aadhar_upload,
        body.bank_account_number.strip(),
        body.ifsc.strip().upper(),
        (body.upi_id or "").strip(),
        _signup_ip,
        _signup_ua,
        _signup_geo.country,
        _signup_geo.region,
        _signup_geo.city,
    )

    await pool.execute(
        "UPDATE otp_verifications SET consumed_at=NOW() WHERE id=$1",
        otp_row["id"],
    )
    await pool.execute(
        "UPDATE otp_verifications SET consumed_at=NOW() WHERE id=$1",
        email_otp_row["id"],
    )

    log_activity(action_type="ACCOUNT_SIGNUP_SUBMIT", request=request,
                 resource_type="user_signup", resource_id=str(signup["id"]),
                 status_code=200, metadata={"email": email, "mobile": phone})
    check_burst(_signup_ip)

    return {
        "success": True,
        "message": "Signup submitted. It is pending admin approval.",
        "signup_id": str(signup["id"]),
    }


@router.get("/portal/users")
async def get_portal_users(user: CurrentUser = Depends(get_admin_user)):
    """Retrieve all crash-course enrollments from the /enroll page."""
    pool = get_pool()

    try:
        users = await pool.fetch(
            """
            SELECT id, name, email, mobile, city, experience_level, interest, learning_goal,
                   ip_details, sms_verified, email_verified, created_at, updated_at
            FROM portal_users
            ORDER BY created_at DESC
            """
        )
        count_result = await pool.fetchval("SELECT COUNT(*) FROM portal_users")

        users_list = [
            {
                "id": str(u["id"]),
                "name": u["name"],
                "email": u["email"],
                "mobile": u["mobile"],
                "city": u["city"],
                "experience_level": u.get("experience_level") or "",
                "interest": u.get("interest") or "",
                "learning_goal": u.get("learning_goal") or "",
                "ip_details": u.get("ip_details") or "",
                "sms_verified": bool(u.get("sms_verified")),
                "email_verified": bool(u.get("email_verified")),
                "created_at": u["created_at"].isoformat() if u["created_at"] else None,
                "updated_at": u["updated_at"].isoformat() if u["updated_at"] else None,
            }
            for u in users
        ]

        log.info(f"Course enrollments retrieved by {user.mobile}: {count_result} total")

        return {
            "users": users_list,
            "total": count_result,
        }

    except Exception as e:
        log.error(f"Error fetching course enrollments: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch course enrollments")


@router.get("/portal/user-signups")
async def get_user_signups(status: str = "PENDING", user: CurrentUser = Depends(get_admin_user)):
    pool = get_pool()
    status_filter = (status or "PENDING").strip().upper()
    if status_filter not in ("PENDING", "APPROVED", "REJECTED", "ALL"):
        raise HTTPException(status_code=400, detail="Invalid status filter")

    try:
        if status_filter == "ALL":
            users = await pool.fetch(
                """
                SELECT id, name, first_name, middle_name, last_name, email, mobile,
                       address, city, state, country,
                       pan_number, aadhar_number,
                       bank_account_number, ifsc, upi_id,
                       ip_details, sms_verified, email_verified,
                       status, rejection_reason, reviewed_at, created_at, updated_at
                FROM user_signups
                ORDER BY created_at DESC
                """
            )
            count_result = await pool.fetchval("SELECT COUNT(*) FROM user_signups")
        else:
            users = await pool.fetch(
                """
                SELECT id, name, first_name, middle_name, last_name, email, mobile,
                       address, city, state, country,
                       pan_number, aadhar_number,
                       bank_account_number, ifsc, upi_id,
                       ip_details, sms_verified, email_verified,
                       status, rejection_reason, reviewed_at, created_at, updated_at
                FROM user_signups
                WHERE status=$1
                ORDER BY created_at DESC
                """,
                status_filter,
            )
            count_result = await pool.fetchval("SELECT COUNT(*) FROM user_signups WHERE status=$1", status_filter)

        users_list = [
            {
                "id": str(u["id"]),
                "name": u["name"],
                "first_name": u.get("first_name") or "",
                "middle_name": u.get("middle_name") or "",
                "last_name": u.get("last_name") or "",
                "email": u["email"],
                "mobile": u["mobile"],
                "address": u.get("address") or "",
                "city": u["city"],
                "state": u.get("state") or "",
                "country": u.get("country") or "",
                "pan_number": u.get("pan_number") or "",
                "aadhar_number": u.get("aadhar_number") or "",
                "bank_account_number": u.get("bank_account_number") or "",
                "ifsc": u.get("ifsc") or "",
                "upi_id": u.get("upi_id") or "",
                "ip_details": u.get("ip_details") or "",
                "sms_verified": bool(u.get("sms_verified")),
                "email_verified": bool(u.get("email_verified")),
                "status": u.get("status") or "PENDING",
                "rejection_reason": u.get("rejection_reason") or "",
                "reviewed_at": u["reviewed_at"].isoformat() if u.get("reviewed_at") else None,
                "created_at": u["created_at"].isoformat() if u["created_at"] else None,
                "updated_at": u["updated_at"].isoformat() if u["updated_at"] else None,
            }
            for u in users
        ]

        log.info(f"User signups retrieved by {user.mobile}: {count_result} total")

        return {
            "users": users_list,
            "total": count_result,
            "status": status_filter,
        }

    except Exception as e:
        log.error(f"Error fetching user signups: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch user signups")


@router.post("/portal/user-signups/{signup_id}/review")
async def review_user_signup(
    signup_id: str,
    body: PortalSignupReviewRequest,
    request: Request,
    user: CurrentUser = Depends(get_admin_user),
):
    action = (body.action or "").strip().upper()
    if action not in ("APPROVE", "REJECT", "RESTORE"):
        raise HTTPException(status_code=400, detail="Action must be APPROVE, REJECT or RESTORE")

    pool = get_pool()
    reason = (body.reason or "").strip()

    async with pool.acquire() as conn:
        async with conn.transaction():
            signup = await conn.fetchrow(
                "SELECT * FROM user_signups WHERE id=$1::uuid FOR UPDATE",
                signup_id,
            )
            if not signup:
                raise HTTPException(status_code=404, detail="Signup not found")

            current_status = (signup.get("status") or "PENDING").strip().upper()

            if action == "RESTORE":
                if current_status != "REJECTED":
                    raise HTTPException(status_code=400, detail="Only rejected signups can be restored")

                await conn.execute(
                    """
                    UPDATE user_signups
                    SET status='PENDING', reviewed_by=NULL, reviewed_at=NULL, rejection_reason=''
                    WHERE id=$1::uuid
                    """,
                    signup_id,
                )
                await _insert_signup_review_log(
                    conn,
                    signup_id=signup_id,
                    action="RESTORE",
                    previous_status=current_status,
                    new_status="PENDING",
                    reason=reason,
                    actor=user,
                )
                log_activity(action_type="SIGNUP_RESTORED", request=request,
                             actor_user_id=str(user.id), actor_name=user.name, actor_role=user.role,
                             resource_type="user_signup", resource_id=signup_id,
                             status_code=200, metadata={"reason": reason})
                return {
                    "success": True,
                    "message": "Signup restored to pending queue",
                }

            if current_status != "PENDING":
                raise HTTPException(status_code=400, detail="Only pending signups can be reviewed")

            if action == "REJECT":
                await conn.execute(
                    """
                    UPDATE user_signups
                    SET status='REJECTED', reviewed_by=$2::uuid, reviewed_at=NOW(), rejection_reason=$3
                    WHERE id=$1::uuid
                    """,
                    signup_id,
                    user.id,
                    reason,
                )
                await _insert_signup_review_log(
                    conn,
                    signup_id=signup_id,
                    action="REJECT",
                    previous_status=current_status,
                    new_status="REJECTED",
                    reason=reason,
                    actor=user,
                )
                log_activity(action_type="SIGNUP_REJECTED", request=request,
                             actor_user_id=str(user.id), actor_name=user.name, actor_role=user.role,
                             resource_type="user_signup", resource_id=signup_id,
                             status_code=200, metadata={"reason": reason})
                return {
                    "success": True,
                    "message": "Signup rejected and removed from pending queue",
                }

            email = _normalize_email(signup.get("email") or "")
            mobile = _normalize_mobile(signup.get("mobile") or "")

            dup_user = await conn.fetchval(
                "SELECT 1 FROM users WHERE mobile=$1 OR lower(email)=lower($2)",
                mobile,
                email,
            )
            if dup_user:
                raise HTTPException(status_code=409, detail="number/email already registered")

            full_name = " ".join(
                [
                    x
                    for x in [
                        (signup.get("first_name") or "").strip(),
                        (signup.get("middle_name") or "").strip(),
                        (signup.get("last_name") or "").strip(),
                    ]
                    if x
                ]
            ).strip() or (signup.get("name") or "").strip() or "User"

            temporary_password = secrets.token_urlsafe(12)
            password_hash = _hash(temporary_password)

            created_user = await conn.fetchrow(
                """
                INSERT INTO users
                    (name, first_name, last_name, email, mobile, password_hash,
                     role, status, is_active,
                     address, country, state, city,
                     aadhar_number, pan_number, upi, bank_account, ifsc_code,
                     aadhar_doc, pan_card_doc)
                VALUES
                    ($1,$2,$3,$4,$5,$6,
                     'USER','ACTIVE',TRUE,
                     $7,$8,$9,$10,
                     $11,$12,$13,$14,$15,
                     $16,$17)
                RETURNING id, user_no
                """,
                full_name,
                (signup.get("first_name") or "").strip() or full_name,
                (signup.get("last_name") or "").strip(),
                email,
                mobile,
                password_hash,
                (signup.get("address") or "").strip(),
                (signup.get("country") or "India").strip() or "India",
                (signup.get("state") or "").strip(),
                (signup.get("city") or "").strip(),
                (signup.get("aadhar_number") or "").strip(),
                (signup.get("pan_number") or "").strip().upper(),
                (signup.get("upi_id") or "").strip(),
                (signup.get("bank_account_number") or "").strip(),
                (signup.get("ifsc") or "").strip().upper(),
                signup.get("aadhar_upload"),
                signup.get("pan_upload"),
            )

            await conn.execute(
                """
                INSERT INTO paper_accounts (user_id, display_name, balance, margin_allotted)
                VALUES ($1, $2, 0, 0)
                ON CONFLICT (user_id) DO NOTHING
                """,
                created_user["id"],
                full_name,
            )

            await conn.execute(
                """
                UPDATE user_signups
                SET status='APPROVED', reviewed_by=$2::uuid, reviewed_at=NOW(), rejection_reason=''
                WHERE id=$1::uuid
                """,
                signup_id,
                user.id,
            )
            await _insert_signup_review_log(
                conn,
                signup_id=signup_id,
                action="APPROVE",
                previous_status=current_status,
                new_status="APPROVED",
                reason=reason,
                actor=user,
            )

    log_activity(action_type="SIGNUP_APPROVED", request=request,
                 actor_user_id=str(user.id), actor_name=user.name, actor_role=user.role,
                 subject_user_id=str(created_user["id"]), subject_name=full_name,
                 resource_type="user_signup", resource_id=signup_id,
                 status_code=200, metadata={"new_user_id": str(created_user["id"])})
    return {
        "success": True,
        "message": "Signup approved and user created as ACTIVE USER",
        "user_id": str(created_user["id"]),
        "user_no": int(created_user["user_no"]),
    }


@router.get("/portal/user-signups/activity")
async def get_user_signup_review_activity(
    limit: int = 25,
    _: CurrentUser = Depends(get_admin_user),
):
    safe_limit = max(1, min(limit, 100))
    pool = get_pool()
    rows = await pool.fetch(
        """
         SELECT log.id, log.signup_id, log.action, log.previous_status, log.new_status, log.reason,
             log.actor_name, log.actor_mobile, log.created_at,
             COALESCE(signup.name, '') AS signup_name,
             COALESCE(signup.email, '') AS signup_email,
             COALESCE(signup.mobile, '') AS signup_mobile
         FROM user_signup_review_log log
         LEFT JOIN user_signups signup ON signup.id = log.signup_id
        ORDER BY log.created_at DESC
        LIMIT $1
        """,
        safe_limit,
    )
    return {
        "items": [_serialize_signup_review_log(row) for row in rows],
        "limit": safe_limit,
    }


@router.post("/portal/users/delete")
async def delete_portal_users(
    body: PortalUsersBulkDeleteRequest,
    user: CurrentUser = Depends(get_admin_user),
):
    """
    Bulk delete portal signup registrations.

    Super admin only.
    """
    user_ids = [str(uid).strip() for uid in (body.user_ids or []) if str(uid).strip()]
    if not user_ids:
        raise HTTPException(status_code=400, detail="No portal user IDs provided")

    pool = get_pool()

    try:
        deleted_count = await pool.fetchval(
            """
            WITH deleted AS (
                DELETE FROM portal_users
                WHERE id = ANY($1::uuid[])
                RETURNING id
            )
            SELECT COUNT(*)::int FROM deleted
            """,
            user_ids,
        )

        return {
            "success": True,
            "deleted": int(deleted_count or 0),
            "requested": len(user_ids),
            "message": f"Deleted {int(deleted_count or 0)} portal signup(s)",
        }

    except HTTPException:
        raise
    except Exception as e:
        # Most common error case here is invalid UUID in user_ids.
        msg = str(e).lower()
        if "uuid" in msg or "invalid input syntax" in msg:
            raise HTTPException(status_code=400, detail="One or more portal user IDs are invalid")

        log.error(f"Error deleting portal users: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete portal users")


@router.post("/password/forgot/send-otp")
async def send_forgot_password_otp(body: ForgotPasswordSendOtpRequest, request: Request):
    return await _send_phone_otp(body.mobile, _OTP_PURPOSE_PASSWORD_RESET, request)


@router.post("/password/forgot/reset")
async def reset_password_with_otp(body: ForgotPasswordResetRequest):
    mobile = _normalize_mobile(body.mobile)
    otp = (body.otp or "").strip()
    new_password = (body.new_password or "").strip()
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    if not re.fullmatch(r"\d{6}", otp):
        raise HTTPException(status_code=400, detail="Enter a valid 6-digit OTP")

    pool = get_pool()
    runtime = await _get_sms_otp_runtime_settings()
    user = await pool.fetchrow(
        "SELECT id, role, is_active FROM users WHERE mobile=$1",
        mobile,
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["role"] != "USER":
        raise HTTPException(status_code=403, detail="Password reset OTP is allowed only for users")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account is inactive")

    row = await pool.fetchrow(
        """
        SELECT id, otp_hash, otp_salt, attempt_count
        FROM otp_verifications
        WHERE contact_type='PHONE'
          AND contact_value=$1
          AND purpose=$2
          AND consumed_at IS NULL
          AND expires_at > NOW()
        ORDER BY created_at DESC
        LIMIT 1
        """,
        mobile,
        _OTP_PURPOSE_PASSWORD_RESET,
    )
    if not row:
        raise HTTPException(status_code=400, detail="OTP expired or not found")

    if int(row["attempt_count"] or 0) >= int(runtime["otp_max_attempts"]):
        raise HTTPException(status_code=429, detail="Too many invalid attempts")

    if _otp_hash(otp, row["otp_salt"]) != row["otp_hash"]:
        await pool.execute(
            "UPDATE otp_verifications SET attempt_count = attempt_count + 1 WHERE id=$1",
            row["id"],
        )
        raise HTTPException(status_code=400, detail="Invalid OTP")

    await pool.execute(
        "UPDATE users SET password_hash=$2 WHERE id=$1::uuid",
        str(user["id"]),
        _hash(new_password),
    )
    await pool.execute(
        "UPDATE otp_verifications SET verified_at=NOW(), consumed_at=NOW() WHERE id=$1",
        row["id"],
    )
    await pool.execute(
        "DELETE FROM user_sessions WHERE user_id=$1::uuid",
        str(user["id"]),
    )
    return {"success": True, "message": "Password reset successfully"}
