"""
app/credentials/credential_store.py
=====================================
Runtime credential store for DhanHQ API token.
Token is auto-refreshed by TokenRefresher using TOTP — no manual rotation
needed.  Falls back to manual rotation via POST /admin/credentials/rotate
if TOTP is not configured.
Persists to system_config table so it survives process restarts.

Phase 10: Security hardening with encryption, masking, and replay protection.
"""
import base64
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.database import get_pool

try:
    from cryptography.fernet import Fernet
    _ENCRYPTION_AVAILABLE = True
except ImportError:
    _ENCRYPTION_AVAILABLE = False

log = logging.getLogger(__name__)

# ── In-memory cache (avoids DB round-trip on every WS connect) ─────────────
_client_id:    str = ""
_access_token: str = ""
_token_expiry: Optional[datetime] = None   # UTC-aware; None = unknown
_auth_mode:    str = "auto_totp"           # 'auto_totp' | 'manual' | 'static_ip'
_static_client_id: str = ""
_static_api_key: str = ""
_static_api_secret: str = ""
_dhan_pin: str = ""
_dhan_totp_secret: str = ""

# ── Phase 10: Encryption key (from environment or generate) ──────────────────
_encryption_cipher: Optional[Fernet] = None


async def _ensure_system_config_table(pool) -> None:
    """Create system_config table if missing (safety net for writes)."""
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS system_config (
            key         VARCHAR(100) PRIMARY KEY,
            value       TEXT,
            updated_at  TIMESTAMPTZ DEFAULT now()
        )
        """
    )

def _get_encryption_cipher() -> Optional[Fernet]:
    """Get or initialize encryption cipher for secrets at rest."""
    global _encryption_cipher
    if _encryption_cipher is not None:
        return _encryption_cipher
    
    if not _ENCRYPTION_AVAILABLE:
        log.warning("[Security] cryptography library not available; secrets not encrypted at rest")
        return None
    
    # Try to load from environment
    key_b64 = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
    if not key_b64:
        log.warning(
            "[Security] No CREDENTIAL_ENCRYPTION_KEY set; "
            "falling back to plaintext-at-rest for runtime credential persistence."
        )
        return None
    
    try:
        _encryption_cipher = Fernet(key_b64)
        return _encryption_cipher
    except Exception as e:
        log.error(f"[Security] Failed to initialize encryption cipher: {e}")
        return None

def _encrypt_secret(secret: str) -> str:
    """Encrypt secret using Fernet (AES-128 in CBC mode)."""
    cipher = _get_encryption_cipher()
    if cipher is None:
        # Return plaintext if encryption unavailable
        return secret
    
    try:
        encrypted = cipher.encrypt(secret.encode())
        return encrypted.decode()  # Return as string
    except Exception as e:
        log.error(f"[Security] Encryption failed: {e}")
        return secret  # Fallback to plaintext

def _decrypt_secret(encrypted: str) -> str:
    """Decrypt secret using Fernet."""
    cipher = _get_encryption_cipher()
    if cipher is None:
        if encrypted.startswith("gAAAAA"):
            log.error(
                "[Security] Encrypted credential found but CREDENTIAL_ENCRYPTION_KEY is missing; "
                "treating value as unavailable until it is re-saved with a stable key configuration."
            )
            return ""
        return encrypted
    
    try:
        decrypted = cipher.decrypt(encrypted.encode())
        return decrypted.decode()
    except Exception as e:
        if encrypted.startswith("gAAAAA"):
            log.error(
                "[Security] Decryption failed for encrypted credential; "
                "value was likely saved with a different CREDENTIAL_ENCRYPTION_KEY: %s",
                e,
            )
            return ""
        log.warning(f"[Security] Decryption failed (may be plaintext): {e}")
        return encrypted  # Fallback to plaintext

def mask_secret(secret: Optional[str], show_chars: int = 4) -> str:
    """Mask secret by showing only last N characters."""
    if not secret:
        return "(none)"
    if len(secret) <= show_chars:
        return "****"
    return "*" * (len(secret) - show_chars) + secret[-show_chars:]

def validate_timestamp_not_replayed(
    timestamp_ms: int,
    tolerance_sec: int = 300,  # 5 minutes
) -> bool:
    """Validate timestamp is not replayed (within tolerance window)."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    tolerance_ms = tolerance_sec * 1000
    
    time_diff = abs(now_ms - timestamp_ms)
    if time_diff > tolerance_ms:
        log.warning(
            f"[Security] Timestamp replay protection: {time_diff}ms > {tolerance_ms}ms (rejected)"
        )
        return False
    
    return True


async def _init_credentials_from_env() -> None:
    """
    Initialize static IP credentials from environment variables if DB is empty.
    This allows deploying with env vars instead of manually entering credentials.
    """
    from app.config import get_settings
    
    cfg = get_settings()
    pool = get_pool()
    
    # Check if database already has static credentials
    existing = await pool.fetchrow(
        "SELECT value FROM system_config WHERE key = 'dhan_static_client_id'"
    )
    
    # Only initialize if DB is empty AND env vars are set
    if existing and existing["value"]:
        return  # Already configured
    
    if not (cfg.dhan_client_id and cfg.dhan_api_key and cfg.dhan_api_secret):
        return  # No env vars to initialize from
    
    log.info("Initializing Dhan credentials from environment variables...")
    
    # Encrypt the secret before storing
    encrypted_secret = _encrypt_secret(cfg.dhan_api_secret)
    
    # Update system_config with env var values
    await pool.executemany(
        "UPDATE system_config SET value=$1, updated_at=now() WHERE key=$2",
        [
            (cfg.dhan_client_id, "dhan_static_client_id"),
            (cfg.dhan_api_key, "dhan_api_key"),
            (encrypted_secret, "dhan_api_secret"),
        ],
    )

    # Best-effort init for auto_totp credentials from environment.
    if cfg.dhan_pin:
        encrypted_pin = _encrypt_secret(cfg.dhan_pin)
        await pool.execute(
            """
            INSERT INTO system_config (key, value)
            VALUES ('dhan_pin', $1)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = now()
            """,
            encrypted_pin,
        )
    if cfg.dhan_totp_secret:
        normalized_totp = cfg.dhan_totp_secret.strip().upper().replace(" ", "")
        encrypted_totp = _encrypt_secret(normalized_totp)
        await pool.execute(
            """
            INSERT INTO system_config (key, value)
            VALUES ('dhan_totp_secret', $1)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = now()
            """,
            encrypted_totp,
        )
    
    log.info(
        f"✓ Credentials initialized from environment: "
        f"client_id={cfg.dhan_client_id[:8]}..., "
        f"api_key={cfg.dhan_api_key[:8]}..., "
        f"api_secret=encrypted"
    )


async def load_credentials() -> None:
    """Load persisted credentials from DB into memory on startup."""
    global _client_id, _access_token, _token_expiry, _auth_mode
    global _static_client_id, _static_api_key, _static_api_secret
    global _dhan_pin, _dhan_totp_secret
    pool = get_pool()
    
    # First, initialize from environment variables if database is empty
    await _init_credentials_from_env()
    
    rows = await pool.fetch(
        "SELECT key, value FROM system_config WHERE key IN ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        "dhan_client_id",
        "dhan_access_token",
        "dhan_token_expiry",
        "auth_mode",
        "dhan_static_client_id",
        "dhan_api_key",
        "dhan_api_secret",
        "dhan_pin",
        "dhan_totp_secret",
    )
    for row in rows:
        if row["key"] == "dhan_client_id":
            _client_id = row["value"] or ""
        elif row["key"] == "dhan_access_token":
            _access_token = row["value"] or ""
        elif row["key"] == "dhan_token_expiry" and row["value"]:
            try:
                _token_expiry = datetime.fromisoformat(row["value"])
                if _token_expiry.tzinfo is None:
                    _token_expiry = _token_expiry.replace(tzinfo=timezone.utc)
            except ValueError:
                _token_expiry = None
        elif row["key"] == "auth_mode" and row["value"] in ("auto_totp", "manual", "static_ip"):
            _auth_mode = row["value"]
        elif row["key"] == "dhan_static_client_id":
            _static_client_id = row["value"] or ""
        elif row["key"] == "dhan_api_key":
            _static_api_key = row["value"] or ""
        elif row["key"] == "dhan_api_secret":
            # Phase 10: Decrypt secret (fallback to plaintext if encryption unavailable)
            encrypted_value = row["value"] or ""
            _static_api_secret = _decrypt_secret(encrypted_value) if encrypted_value else ""
        elif row["key"] == "dhan_pin":
            encrypted_value = row["value"] or ""
            _dhan_pin = _decrypt_secret(encrypted_value) if encrypted_value else ""
        elif row["key"] == "dhan_totp_secret":
            encrypted_value = row["value"] or ""
            _dhan_totp_secret = _decrypt_secret(encrypted_value) if encrypted_value else ""

    # Local/mock-friendly fallback: if DB has no runtime credentials yet,
    # hydrate from environment so startup streams can still initialize.
    if not _client_id:
        from app.config import get_settings
        cfg = get_settings()
        _client_id = (cfg.dhan_client_id or "").strip()
    if not _access_token:
        from app.config import get_settings
        cfg = get_settings()
        _access_token = (cfg.dhan_access_token or "").strip()
    if not _dhan_pin:
        from app.config import get_settings
        cfg = get_settings()
        _dhan_pin = (cfg.dhan_pin or "").strip()
    if not _dhan_totp_secret:
        from app.config import get_settings
        cfg = get_settings()
        _dhan_totp_secret = (cfg.dhan_totp_secret or "").strip().upper().replace(" ", "")
    log.info(
        f"Credentials loaded — client_id={'set' if _client_id else 'EMPTY'}, "
        f"token={'set' if _access_token else 'EMPTY'}, "
        f"expiry={_token_expiry.isoformat() if _token_expiry else 'unknown'}, "
        f"auth_mode={_auth_mode}, "
        f"static_client_id={'set' if _static_client_id else 'EMPTY'}, "
        f"static_key={'set' if _static_api_key else 'EMPTY'}, "
        f"static_secret={'set' if _static_api_secret else 'EMPTY'} (decrypted), "
        f"dhan_pin={'set' if _dhan_pin else 'EMPTY'}, "
        f"dhan_totp_secret={'set' if _dhan_totp_secret else 'EMPTY'}"
    )


async def rotate_token(
    new_token: str,
    *,
    expiry: Optional[datetime] = None,
    reconnect: bool = True,
) -> None:
    """
    Update access token (and optional expiry) in memory + DB.
    Triggers a graceful reconnect of all 5 WebSockets if reconnect=True.
    """
    import traceback
    global _access_token, _token_expiry
    _access_token = new_token
    if expiry is not None:
        _token_expiry = expiry

    try:
        pool = get_pool()
        await _ensure_system_config_table(pool)
        # Persist token
        await pool.execute(
            """
            INSERT INTO system_config (key, value)
            VALUES ('dhan_access_token', $1)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = now()
            """,
            new_token,
        )
        # Persist expiry (upsert — row may or may not exist yet)
        if expiry is not None:
            await pool.execute(
                """
                INSERT INTO system_config (key, value)
                VALUES ('dhan_token_expiry', $1)
                ON CONFLICT (key) DO UPDATE
                  SET value = EXCLUDED.value, updated_at = now()
                """,
                expiry.isoformat(),
            )
        log.info(
            f"Access token rotated and persisted. "
            f"Expiry: {_token_expiry.isoformat() if _token_expiry else 'unknown'}"
        )
    except Exception as e:
        log.error(f"Failed to persist token to DB: {str(e)}")
        log.error(traceback.format_exc())
        # Token is still updated in memory, but log the DB persistence failure
        raise  # Re-raise so caller knows DB update failed

    if reconnect:
        try:
            # Import here to avoid circular import
            from app.market_data.websocket_manager import ws_manager
            from app.market_data.depth_ws_manager import depth_ws_manager
            
            log.info("Attempting to reconnect WebSockets after token rotation...")
            try:
                await ws_manager.reconnect_all()
                log.info("✅ WebSocket manager reconnected successfully")
            except Exception as e:
                log.warning(f"⚠️  WebSocket manager reconnect failed: {str(e)}")
            
            try:
                await depth_ws_manager.reconnect()
                log.info("✅ Depth WebSocket manager reconnected successfully")
            except Exception as e:
                log.warning(f"⚠️  Depth WebSocket manager reconnect failed: {str(e)}")
        except ImportError as e:
            log.warning(f"Could not import WebSocket managers (likely not initialized yet): {str(e)}")
        except Exception as e:
            log.warning(f"⚠️  WebSocket reconnection encountered an error: {str(e)}")
            log.warning(traceback.format_exc())
            # Don't raise — token is still updated, just WS reconnect failed


async def update_client_id(new_client_id: str) -> None:
    """Update client ID in memory and database."""
    import traceback
    global _client_id
    _client_id = new_client_id
    try:
        pool = get_pool()
        await _ensure_system_config_table(pool)
        await pool.execute(
            """
            INSERT INTO system_config (key, value)
            VALUES ('dhan_client_id', $1)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = now()
            """,
            new_client_id,
        )
        log.info(f"✅ Client ID updated: {new_client_id[:10]}...")
    except Exception as e:
        log.error(f"Failed to persist client_id to DB: {str(e)}")
        log.error(traceback.format_exc())
        raise  # Re-raise so caller knows DB update failed


def get_client_id() -> str:
    return _client_id


def get_rest_client_id() -> str:
    """Return the client ID that should be used for REST authentication."""
    if get_active_auth_mode() == "static_ip" and is_static_configured():
        return _static_client_id
    return _client_id


def get_access_token() -> str:
    return _access_token


def get_token_expiry() -> Optional[datetime]:
    """Return the UTC-aware expiry of the current token, or None if unknown."""
    return _token_expiry


def is_token_expiring_soon(within_minutes: int = 120) -> bool:
    """True when the token expires within `within_minutes` or is already expired."""
    if _token_expiry is None:
        return True  # Unknown expiry → assume refresh needed
    threshold = datetime.now(tz=timezone.utc)
    from datetime import timedelta
    return _token_expiry <= (threshold + timedelta(minutes=within_minutes))


def get_auth_mode() -> str:
    """Return current auth mode: 'auto_totp', 'manual', or 'static_ip'."""
    return _auth_mode


def get_active_auth_mode() -> str:
    """Return effective auth mode with safety fallback."""
    if _auth_mode in ("auto_totp", "manual", "static_ip"):
        return _auth_mode
    if is_static_configured():
        return "static_ip"
    return "auto_totp"


async def set_auth_mode(mode: str) -> None:
    """
    Persist the auth mode to DB and update in-memory cache.
    mode must be 'auto_totp', 'manual', or 'static_ip'.
    """
    import traceback
    global _auth_mode
    if mode not in ("auto_totp", "manual", "static_ip"):
        raise ValueError(
            f"Invalid auth_mode '{mode}'. Must be 'auto_totp', 'manual', or 'static_ip'."
        )
    _auth_mode = mode
    try:
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO system_config (key, value)
            VALUES ('auth_mode', $1)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = now()
            """,
            mode,
        )
        log.info(f"✅ auth_mode set to '{mode}' and persisted.")
    except Exception as e:
        log.error(f"Failed to persist auth_mode to DB: {str(e)}")
        log.error(traceback.format_exc())
        raise  # Re-raise so caller knows DB update failed


def get_static_credentials(*, masked: bool = False) -> dict:
    """Return static credentials. Masked form only exposes a prefix/suffix."""
    def _mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}****{value[-4:]}"

    if masked:
        return {
            "client_id": _mask(_static_client_id),
            "api_key": _mask(_static_api_key),
            "api_secret": _mask(_static_api_secret),
        }
    return {
        "client_id": _static_client_id,
        "api_key": _static_api_key,
        "api_secret": _static_api_secret,
    }


def get_totp_credentials(*, masked: bool = False) -> dict:
    """Return persisted auto_totp credentials (PIN + TOTP secret)."""
    if masked:
        return {
            "pin": "******" if _dhan_pin else "",
            "totp_secret": mask_secret(_dhan_totp_secret) if _dhan_totp_secret else "",
        }
    return {
        "pin": _dhan_pin,
        "totp_secret": _dhan_totp_secret,
    }


def is_totp_configured() -> bool:
    return bool(_dhan_pin and _dhan_totp_secret)


def is_static_configured() -> bool:
    return bool(_static_client_id and _static_api_key and _static_api_secret)


async def update_static_credentials(
    *,
    static_client_id: str,
    api_key: str,
    api_secret: str,
) -> None:
    """Persist static IP credentials to DB and update cache.
    Phase 10: API secret is encrypted before storage.
    """
    import traceback
    global _static_client_id, _static_api_key, _static_api_secret
    _static_client_id = static_client_id
    _static_api_key = api_key
    _static_api_secret = api_secret  # Stored plaintext in memory
    
    try:
        # Encrypt secret before persisting to DB
        encrypted_secret = _encrypt_secret(api_secret)
        
        pool = get_pool()
        await _ensure_system_config_table(pool)
        await pool.executemany(
            """
            INSERT INTO system_config (key, value)
            VALUES ($2, $1)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = now()
            """,
            [
                (static_client_id, "dhan_static_client_id"),
                (api_key, "dhan_api_key"),
                (encrypted_secret, "dhan_api_secret"),
            ],
        )
        log.info(
            f"✅ Static credentials updated: client_id={'set' if static_client_id else 'EMPTY'}, "
            f"api_key={'set' if api_key else 'EMPTY'}, "
            f"api_secret=encrypted({mask_secret(api_secret)})"
        )
    except Exception as e:
        log.error(f"Failed to persist static credentials to DB: {str(e)}")
        log.error(traceback.format_exc())
        raise  # Re-raise so caller knows DB update failed


async def update_totp_credentials(*, pin: str, totp_secret: str) -> None:
    """Persist auto_totp credentials to DB and update cache."""
    import traceback
    global _dhan_pin, _dhan_totp_secret

    normalized_pin = (pin or "").strip()
    normalized_totp = (totp_secret or "").strip().upper().replace(" ", "")
    _dhan_pin = normalized_pin
    _dhan_totp_secret = normalized_totp

    try:
        encrypted_pin = _encrypt_secret(normalized_pin)
        encrypted_totp = _encrypt_secret(normalized_totp)
        pool = get_pool()
        await _ensure_system_config_table(pool)
        await pool.executemany(
            """
            INSERT INTO system_config (key, value)
            VALUES ($2, $1)
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = now()
            """,
            [
                (encrypted_pin, "dhan_pin"),
                (encrypted_totp, "dhan_totp_secret"),
            ],
        )
        log.info(
            "✅ Auto TOTP credentials updated: pin=%s, totp_secret=%s",
            "set" if normalized_pin else "EMPTY",
            "set" if normalized_totp else "EMPTY",
        )
    except Exception as e:
        log.error(f"Failed to persist auto_totp credentials to DB: {str(e)}")
        log.error(traceback.format_exc())
        raise


def get_ws_url(*, include_version: bool = True) -> str:
    """Build the DhanHQ Live Market Feed WebSocket URL."""
    from app.config import get_settings
    cfg = get_settings()

    params = {
        "token": _access_token,
        "clientId": _client_id,
        "authType": "2",
    }
    if include_version:
        params["version"] = "2"
    base = cfg.dhan_feed_url.strip() or "wss://api-feed.dhan.co"
    return _append_query(base, params)


def get_ws_url_candidates() -> list[str]:
    """Return WS URLs to try, in preference order."""
    return [get_ws_url(include_version=True), get_ws_url(include_version=False)]


def get_depth_ws_url() -> str:
    """Build the 20-Level Full Market Depth WebSocket URL."""
    from app.config import get_settings
    cfg = get_settings()
    base = cfg.dhan_depth_20_url.strip() or "wss://depth-api-feed.dhan.co/twentydepth"
    return _append_query(base, {"token": _access_token, "clientId": _client_id, "authType": "2"})


def _append_query(base_url: str, params: dict[str, str]) -> str:
    """Append params to URL while preserving existing query string."""
    split = urlsplit(base_url)
    existing = dict(parse_qsl(split.query, keep_blank_values=True))
    existing.update(params)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(existing), split.fragment))


def serialize_static_body(body: Optional[dict]) -> str:
    """Serialize request body deterministically for static-IP signature parity."""
    if body is None:
        return ""
    return json.dumps(body, separators=(",", ":"), sort_keys=True)


def get_static_post_self_test() -> dict:
    """Return a safe diagnostic snapshot for static-IP POST signing readiness."""
    sample_path = "/optionchain"
    rest_client_id = (get_rest_client_id() or "").strip()
    sample_body = {
        "UnderlyingScrip": "12345",
        "UnderlyingSeg": "IDX_I",
        "Expiry": "2099-12-31",
        "dhanClientId": rest_client_id,
    }
    payload = serialize_static_body(sample_body)
    headers = get_rest_headers(
        method="POST",
        path=sample_path,
        body=sample_body,
        body_payload=payload,
    )
    header_client_id = (headers.get("client-id") or "").strip()
    signature = headers.get("signature") or ""
    access_token = headers.get("access-token") or ""
    active_mode = get_active_auth_mode()
    using_static_headers = active_mode == "static_ip" and is_static_configured()
    payload_hash = sha256(payload.encode("utf-8")).hexdigest() if payload else ""

    return {
        "active_mode": active_mode,
        "using_static_headers": using_static_headers,
        "static_configured": is_static_configured(),
        "rest_client_id": rest_client_id,
        "header_client_id": header_client_id,
        "body_client_id": sample_body["dhanClientId"],
        "client_id_match": bool(rest_client_id and rest_client_id == header_client_id == sample_body["dhanClientId"]),
        "signature_present": bool(signature),
        "signature_prefix": signature[:12] if signature else "",
        "timestamp_present": bool(headers.get("timestamp")),
        "access_token_present": bool(access_token),
        "sample_path": sample_path,
        "payload_sha256": payload_hash,
        "payload_preview": payload[:160],
        "content_type": headers.get("Content-Type"),
        "status": (
            "ready"
            if using_static_headers and rest_client_id and rest_client_id == header_client_id == sample_body["dhanClientId"] and bool(signature)
            else "not_ready"
        ),
    }


def _build_static_signature(path: str, body: Optional[dict], timestamp: str, body_payload: Optional[str] = None) -> str:
    """Build HMAC-SHA256 signature using (timestamp + path + exact json_body)."""
    payload = body_payload if body_payload is not None else serialize_static_body(body)
    message = f"{timestamp}{path}{payload}"
    # Note: _static_api_secret is stored plaintext in memory (decrypted on load)
    return hmac.new(
        _static_api_secret.encode(),
        message.encode(),
        sha256,
    ).hexdigest()


def _get_static_headers(path: str, body: Optional[dict], body_payload: Optional[str] = None) -> dict:
    """Build HTTP headers for static IP authentication.
    Phase 10: Enhanced with millisecond timestamp precision for accuracy.
    Secret is used in plaintext from memory (decrypted on load).
    """
    # Timestamp in milliseconds (as per Dhan API spec)
    ts_ms = str(int(time.time() * 1000))
    signature = _build_static_signature(path, body, ts_ms, body_payload=body_payload)
    return {
        "access-token": _static_api_key,
        "client-id":    _static_client_id,
        "timestamp":    ts_ms,
        "signature":    signature,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }


def get_rest_headers(*, method: str, path: str, body: Optional[dict] = None, body_payload: Optional[str] = None) -> dict:
    active_mode = get_active_auth_mode()
    if active_mode == "static_ip" and is_static_configured():
        return _get_static_headers(path, body, body_payload=body_payload)
    return {
        "access-token": _access_token,
        "client-id":    _client_id,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
