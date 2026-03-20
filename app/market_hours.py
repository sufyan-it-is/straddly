"""
app/market_hours.py
===================
IST market hours — hardcoded per exchange.
Time zone: Asia/Kolkata (GMT+5:30).
Exchange time is ground-truthed via ltt from the first WebSocket tick.
"""
import logging
import os
import zoneinfo
from datetime import datetime, time, date
from enum import Enum

log = logging.getLogger(__name__)

IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def _force_market_open_enabled() -> bool:
    """Allow QA to force market-open behavior via environment variable."""
    v = os.getenv("FORCE_MARKET_OPEN", "false").strip().lower()
    return v in ("1", "true", "yes", "on")


def _force_worker_windows_enabled() -> bool:
    """Allow local QA to keep market-driven workers active while market_state remains real."""
    v = os.getenv("FORCE_RUN_MARKET_WORKERS", "false").strip().lower()
    return v in ("1", "true", "yes", "on")

# Commodities that trade until 23:55 IST (international price-linked)
MCX_INTL_COMMODITIES: frozenset[str] = frozenset({
    "GOLD", "GOLDM", "GOLDGUINEA", "GOLDPETAL", "GOLDTEN",
    "SILVER", "SILVERM", "SILVERMIC",
    "CRUDEOIL", "CRUDEOILM",
    "NATURALGAS", "NATGASMINI",
})


class MarketState(str, Enum):
    PRE_OPEN   = "PRE_OPEN"    # 09:00–09:15 NSE/BSE only
    OPEN       = "OPEN"
    POST_CLOSE = "POST_CLOSE"  # 15:30–15:40 NSE closing session
    CLOSED     = "CLOSED"


# ── Hardcoded session windows (IST) ────────────────────────────────────────

_NSE_BSE_PRE_OPEN  = (time(9, 0),   time(9, 15))
_NSE_BSE_SESSION   = (time(9, 15),  time(15, 30))
_NSE_BSE_POST      = (time(15, 30), time(15, 40))

_MCX_SESSION       = (time(9, 0),   time(23, 30))
_MCX_INTL_SESSION  = (time(9, 0),   time(23, 55))

# Holidays — loaded from exchange_holidays table at startup
# Format: {"NSE": set[date], "BSE": set[date], "MCX": set[date]}
_EXCHANGE_HOLIDAYS: dict[str, set[date]] = {
    "NSE": set(),
    "BSE": set(),
    "MCX": set(),
}


def _is_holiday(d: date) -> bool:
    """Check if date is a holiday (legacy function for NSE/BSE)."""
    # Check NSE holidays (most common)
    if d in _EXCHANGE_HOLIDAYS.get("NSE", set()):
        return True
    # Check for weekends
    return d.weekday() >= 5  # Sat=5, Sun=6


def _is_exchange_holiday(exchange: str, d: date) -> bool:
    """Check if date is a holiday for a specific exchange."""
    if d in _EXCHANGE_HOLIDAYS.get(exchange, set()):
        return True
    return d.weekday() >= 5  # All exchanges closed on weekends


def get_market_state(exchange_segment: str, symbol: str = "") -> MarketState:
    """
    Returns the current MarketState for a given exchange segment.

    exchange_segment examples: NSE_EQ, NSE_FNO, BSE_EQ, MCX_FO, IDX_I
    """
    if _force_market_open_enabled():
        return MarketState.OPEN

    now_ist = datetime.now(tz=IST)
    today   = now_ist.date()
    now_t   = now_ist.time()

    seg = exchange_segment.upper()

    # Determine exchange for holiday check
    if seg in ("NSE_EQ", "NSE_FNO", "IDX_I"):
        if _is_exchange_holiday("NSE", today):
            return MarketState.CLOSED
    elif seg in ("BSE_EQ", "BSE_FNO"):
        if _is_exchange_holiday("BSE", today):
            return MarketState.CLOSED
    elif seg in ("MCX_FO", "MCX_EQ"):
        if _is_exchange_holiday("MCX", today):
            return MarketState.CLOSED
    else:
        # Fallback
        if _is_holiday(today):
            return MarketState.CLOSED

    # NSE / BSE (equity + derivatives + index)
    if seg in ("NSE_EQ", "NSE_FNO", "BSE_EQ", "BSE_FNO", "IDX_I"):
        if _NSE_BSE_PRE_OPEN[0] <= now_t < _NSE_BSE_PRE_OPEN[1]:
            return MarketState.PRE_OPEN
        if _NSE_BSE_SESSION[0] <= now_t <= _NSE_BSE_SESSION[1]:
            return MarketState.OPEN
        if _NSE_BSE_POST[0] < now_t <= _NSE_BSE_POST[1]:
            return MarketState.POST_CLOSE
        return MarketState.CLOSED

    # MCX
    if seg in ("MCX_FO", "MCX_EQ"):
        sym = symbol.upper()
        s, e = _MCX_INTL_SESSION if sym in MCX_INTL_COMMODITIES else _MCX_SESSION
        if s <= now_t <= e:
            return MarketState.OPEN
        return MarketState.CLOSED

    return MarketState.CLOSED


def is_market_open(exchange_segment: str, symbol: str = "") -> bool:
    return get_market_state(exchange_segment, symbol) == MarketState.OPEN


# ── Scheduler-friendly session helpers (IST) ──────────────────────────────

def is_equity_window_active(now_ist: datetime | None = None) -> bool:
    """True during NSE/BSE pre-open + session: 09:00–15:30 IST on trading days."""
    if _force_market_open_enabled() or _force_worker_windows_enabled():
        return True
    now_ist = now_ist or datetime.now(tz=IST)
    today = now_ist.date()
    if _is_exchange_holiday("NSE", today) and _is_exchange_holiday("BSE", today):
        return False
    t = now_ist.time()
    return _NSE_BSE_PRE_OPEN[0] <= t <= _NSE_BSE_SESSION[1]


def is_commodity_window_active(now_ist: datetime | None = None) -> bool:
    """True during MCX session: 09:00–23:30 IST on trading days."""
    if _force_market_open_enabled() or _force_worker_windows_enabled():
        return True
    now_ist = now_ist or datetime.now(tz=IST)
    today = now_ist.date()
    if _is_exchange_holiday("MCX", today):
        return False
    t = now_ist.time()
    return _MCX_SESSION[0] <= t <= _MCX_SESSION[1]


def is_any_market_window_active(now_ist: datetime | None = None) -> bool:
    now_ist = now_ist or datetime.now(tz=IST)
    return is_equity_window_active(now_ist) or is_commodity_window_active(now_ist)


def is_nse_bse_ws_window_open_strict(now_ist: datetime | None = None) -> bool:
    """
    Strict NSE/BSE websocket window gate.

    Unlike scheduler helpers, this intentionally ignores FORCE_RUN_MARKET_WORKERS
    so outbound Dhan WS connections are never started outside the real
    NSE/BSE pre-open + session window.
    """
    if _force_market_open_enabled():
        return True

    now_ist = now_ist or datetime.now(tz=IST)
    today = now_ist.date()
    if _is_exchange_holiday("NSE", today) and _is_exchange_holiday("BSE", today):
        return False
    t = now_ist.time()
    return _NSE_BSE_PRE_OPEN[0] <= t <= _NSE_BSE_SESSION[1]


def _next_trading_day(exchange: str, start: date) -> date:
    d = start
    # avoid infinite loops; 14 days is plenty for consecutive holidays+weekends
    for _ in range(14):
        if not _is_exchange_holiday(exchange, d):
            return d
        d = d.fromordinal(d.toordinal() + 1)
    return start


def next_equity_open_ist(now_ist: datetime | None = None) -> datetime:
    now_ist = now_ist or datetime.now(tz=IST)
    today = now_ist.date()
    candidate = datetime.combine(today, _NSE_BSE_PRE_OPEN[0], tzinfo=IST)
    if now_ist < candidate and not (_is_exchange_holiday("NSE", today) and _is_exchange_holiday("BSE", today)):
        return candidate
    next_day = _next_trading_day("NSE", today.fromordinal(today.toordinal() + 1))
    return datetime.combine(next_day, _NSE_BSE_PRE_OPEN[0], tzinfo=IST)


def next_commodity_open_ist(now_ist: datetime | None = None) -> datetime:
    now_ist = now_ist or datetime.now(tz=IST)
    today = now_ist.date()
    candidate = datetime.combine(today, _MCX_SESSION[0], tzinfo=IST)
    if now_ist < candidate and not _is_exchange_holiday("MCX", today):
        return candidate
    next_day = _next_trading_day("MCX", today.fromordinal(today.toordinal() + 1))
    return datetime.combine(next_day, _MCX_SESSION[0], tzinfo=IST)


# ── Staleness thresholds (seconds) — only meaningful when market is OPEN ───

_STALE_THRESH: dict[str, int] = {
    "NSE_EQ":  30,
    "NSE_FNO": 30,
    "BSE_EQ":  30,
    "IDX_I":   15,
    "MCX_FO":  60,
}


def is_stale(updated_at: datetime, exchange_segment: str, symbol: str = "") -> bool:
    """
    Returns True only during OPEN state if the row hasn't been updated
    within the staleness threshold. Never True while CLOSED.
    """
    if get_market_state(exchange_segment, symbol) != MarketState.OPEN:
        return False
    if updated_at is None:
        return True
    threshold = _STALE_THRESH.get(exchange_segment.upper(), 30)
    age = (datetime.now(tz=IST) - updated_at).total_seconds()
    return age > threshold


# ── Exchange time sync ─────────────────────────────────────────────────────
# Stores the skew between our clock and the exchange's ltt field.
# Only used for staleness monitoring — we never adjust the system clock.

_exchange_time_skew_ms: float = 0.0


def record_exchange_tick_time(ltt: datetime) -> None:
    """Call once per WS connection after first tick arrives."""
    global _exchange_time_skew_ms
    our_now  = datetime.now(tz=IST)
    ltt_ist  = ltt.astimezone(IST) if ltt.tzinfo else ltt.replace(tzinfo=IST)
    skew_ms  = (our_now - ltt_ist).total_seconds() * 1000
    _exchange_time_skew_ms = skew_ms
    if abs(skew_ms) > 2000:
        log.warning(
            f"Exchange time skew is {skew_ms:.0f}ms — check system clock."
        )


def get_exchange_skew_ms() -> float:
    return _exchange_time_skew_ms


# ── Holiday loading from database ──────────────────────────────────────────

async def load_exchange_holidays_from_db() -> bool:
    """
    Load trading holidays from database into memory.
    Called during app startup.
    
    Returns True if successfully loaded, False otherwise.
    """
    try:
        from app.database import get_pool
    except ImportError:
        log.warning("Database not available; using empty holidays set")
        return False
    
    try:
        pool = get_pool()
        if not pool:
            return False
        
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT exchange, holiday_date FROM exchange_holidays "
                "ORDER BY exchange, holiday_date"
            )
            
            global _EXCHANGE_HOLIDAYS
            _EXCHANGE_HOLIDAYS = {"NSE": set(), "BSE": set(), "MCX": set()}
            
            for row in rows:
                exchange = row["exchange"]
                holiday_date = row["holiday_date"]
                if exchange in _EXCHANGE_HOLIDAYS:
                    _EXCHANGE_HOLIDAYS[exchange].add(holiday_date)
            
            total = sum(len(v) for v in _EXCHANGE_HOLIDAYS.values())
            log.info(f"Loaded {total} exchange holidays from database")
            return True
    
    except Exception as exc:
        log.error(f"Failed to load exchange holidays from database: {exc}")
        return False
