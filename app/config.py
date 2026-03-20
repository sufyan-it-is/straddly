"""
app/config.py
=============
All environment-driven settings loaded once at startup.
Use python-dotenv for local dev; real deployments inject env vars directly.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── PostgreSQL ──────────────────────────────────────────────────────────
    database_url: str = "postgresql://postgres:postgres@localhost:5432/trading_terminal"

    # ── DhanHQ (initial values — can be overridden at runtime via Admin API) ─
    dhan_client_id:    str = ""
    dhan_access_token: str = ""  # optional when TOTP auto-refresh is configured
    dhan_base_url:     str = "https://api.dhan.co/v2"
    dhan_feed_url:     str = "wss://api-feed.dhan.co"
    dhan_depth_20_url: str = "wss://depth-api-feed.dhan.co/twentydepth"

    # ── DhanHQ Static IP credentials (Mode B) ─────────────────────────────────
    # These are read from environment variables and saved to DB on first startup
    dhan_api_key:      str = ""   # Static IP API key
    dhan_api_secret:   str = ""   # Static IP API secret

    # ── DhanHQ TOTP auto-refresh (permanent token — no manual rotation) ──────
    # Set both to enable headless 24-hour token renewal.
    dhan_pin:          str = ""   # 6-digit Dhan login PIN
    dhan_totp_secret:  str = ""   # TOTP shared secret shown on Dhan TOTP setup page

    # ── App ─────────────────────────────────────────────────────────────────
    debug:             bool = False
    log_level:         str  = "INFO"
    cors_origins_raw:  str  = "*"  # Allow all origins (secured by Coolify + authenticated endpoints)

    # ── SMS / OTP ────────────────────────────────────────────────────────────
    message_central_customer_id: str = ""
    message_central_password: str = ""
    otp_expiry_seconds: int = 300
    otp_resend_cooldown_seconds: int = 60
    otp_max_attempts: int = 5

    # ── Email OTP (external service) ───────────────────────────────────────
    email_otp_service_base_url: str = "https://nexus-otp-server.vercel.app"
    email_otp_service_timeout_seconds: int = 15
    email_otp_expiry_seconds: int = 300

    # ── Security alert thresholds (tunable via env) ──────────────────────────
    # Unusual activity burst detection
    security_burst_window_seconds: int = 300          # rolling window to count actions
    security_burst_actions_threshold: int = 60        # total actions/IP triggering warning
    security_sensitive_burst_threshold: int = 20      # login/OTP actions/IP triggering warning
    # Impossible travel detection (applies after successful logins with geo data)
    security_impossible_travel_window_minutes: int = 120   # max window between two logins
    security_impossible_travel_speed_kmh: float = 900.0    # alert if implied speed exceeds this
    # Repeated failed OTP attempt detection
    security_otp_fail_window_minutes: int = 15        # rolling window
    security_otp_fail_threshold_per_contact: int = 5  # failures on same contact
    security_otp_fail_threshold_per_ip: int = 20      # failures from same IP

    # ── Geoip ────────────────────────────────────────────────────────────────
    geoip_db_path: str = "/app/geoip/GeoLite2-City.mmdb"  # local MaxMind DB path

    @property
    def cors_origins(self) -> list[str]:
        """Return CORS origins list."""
        origins_raw = self.cors_origins_raw.strip()
        # If "*" (allow all), return ["*"]
        if origins_raw == "*":
            return ["*"]
        # Otherwise parse comma-separated list
        return [o.strip() for o in origins_raw.split(",") if o.strip()]

    # ── Market data ─────────────────────────────────────────────────────────
    tick_batch_ms:         int   = 100     # flush tick buffer every N ms
    greeks_poll_seconds:   int   = 15      # REST /optionchain poll interval
    max_ws_connections:    int   = 5
    max_tokens_per_ws:     int   = 5000
    max_msg_instruments:   int   = 100     # DhanHQ limit per JSON message

    # ── 20-Level Depth instruments (get top-20 bid/ask) ─────────────────────
    # Index spot tokens — fetched from instrument_master at startup
    depth_20_underlying: list[str] = ["NIFTY", "BANKNIFTY", "SENSEX"]

    # ── Startup safety flags ─────────────────────────────────────────────────
    # Set DISABLE_DHAN_WS=true or STARTUP_START_STREAMS=false in .env to
    # prevent any outbound connections to DhanHQ servers on startup.
    # Use this for local dev / testing to avoid conflicting with production.
    disable_dhan_ws:        bool = False  # DISABLE_DHAN_WS
    disable_market_streams: bool = False  # DISABLE_MARKET_STREAMS
    startup_start_streams:  bool = True   # STARTUP_START_STREAMS
    startup_load_master:    bool = True   # STARTUP_LOAD_MASTER
    startup_load_tier_b:    bool = True   # STARTUP_LOAD_TIER_B
    startup_refresh_margin: bool = False  # STARTUP_REFRESH_MARGIN

    @property
    def dhan_disabled(self) -> bool:
        """True if ALL DhanHQ outbound connections should be skipped."""
        return (
            self.disable_dhan_ws
            or self.disable_market_streams
            or not self.startup_start_streams
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
