"""
app/instruments/subscription_manager.py
=========================================
Manages which instruments are subscribed on which WebSocket slot.

Tier-B: always subscribed, ws_slot from instrument_master (hash-based).
Tier-A: subscribed on-demand when user adds to watchlist or has open position.
         Unsubscribed ONLY when instrument expires AND (no watchlist + no position).

On reconnect the subscription map is replayed idempotently.
"""
import asyncio
import logging
from collections import defaultdict
from datetime import date

from app.database import get_pool
from app.runtime.notifications import add_notification

log = logging.getLogger(__name__)

# ws_slot → set of instrument_tokens currently subscribed
_active: dict[int, set[int]] = {i: set() for i in range(5)}

# instrument_token → ws_slot  (for fast reverse lookup)
_token_to_slot: dict[int, int] = {}

_lock = asyncio.Lock()


# ── Tier-B startup ──────────────────────────────────────────────────────────

async def initialise_tier_b() -> dict:
    """
    Load all Tier-B tokens from DB into the subscription map.
    Called once at startup — ws_manager.subscribe_all() reads _active.
    Returns stats dict with counts.
    """
    pool = get_pool()
    
    # Validate database connection
    try:
        rows = await pool.fetch(
            "SELECT instrument_token, ws_slot FROM instrument_master "
            "WHERE tier = 'B' AND ws_slot IS NOT NULL "
            "AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)"
        )
    except Exception as exc:
        log.error(f"Database query failed during Tier-B init: {exc}")
        raise
    
    if not rows:
        log.warning("No Tier-B instruments found in database — subscription map will be empty")
        await add_notification(
            category="live_feed",
            severity="warning",
            title="Tier-B subscription map empty",
            message="No Tier-B instruments found in database; live feed will have no baseline subscriptions.",
            dedupe_key="tierb-empty",
            dedupe_ttl_seconds=1800,
        )
        return {"total": 0, "slots": {i: 0 for i in range(5)}}
    
    # Validate ws_slot values
    invalid_slots = [r for r in rows if r["ws_slot"] not in range(5)]
    if invalid_slots:
        log.warning(
            f"Found {len(invalid_slots)} instruments with invalid ws_slot values "
            f"(must be 0-4). These will be skipped."
        )
        await add_notification(
            category="live_feed",
            severity="warning",
            title="Invalid WS slots in instrument master",
            message=f"{len(invalid_slots)} instruments have invalid ws_slot values and were skipped during Tier-B load.",
            dedupe_key="tierb-invalid-slots",
            dedupe_ttl_seconds=1800,
        )
    
    # Build subscription map
    async with _lock:
        loaded_count = 0
        for row in rows:
            token = row["instrument_token"]
            slot  = row["ws_slot"]
            
            if slot not in range(5):
                continue  # Skip invalid slots
                
            _active[slot].add(token)
            _token_to_slot[token] = slot
            loaded_count += 1

    total = sum(len(s) for s in _active.values())
    slot_breakdown = ", ".join(f"WS-{i}: {len(_active[i])}" for i in range(5))
    
    if total == 0:
        log.error("Tier-B initialization completed but NO tokens were loaded!")
        raise RuntimeError("Tier-B subscription map is empty after initialization")
    
    log.info(
        f"Tier-B subscription map ready — {total} tokens across 5 WS slots: {slot_breakdown}"
    )
    
    return {"total": total, "slots": {i: len(_active[i]) for i in range(5)}}


async def initialise_tier_a_from_state() -> dict:
    """
    Rehydrate Tier-A subscriptions from persisted user state.

    Sources:
      - watchlist_items (active watchlists)
      - open paper_positions (quantity != 0)

    This runs at startup/connect so equity/watchlist prices resume immediately
    after restart without waiting for frontend re-subscribe events.
    """
    pool = get_pool()
    rows = await pool.fetch(
        """
        WITH user_interest AS (
            SELECT DISTINCT wi.instrument_token
            FROM watchlist_items wi
            UNION
            SELECT DISTINCT pp.instrument_token
            FROM paper_positions pp
            WHERE pp.status = 'OPEN'
              AND pp.quantity != 0
        )
        SELECT ui.instrument_token
        FROM user_interest ui
        JOIN instrument_master im ON im.instrument_token = ui.instrument_token
        WHERE im.tier = 'A'
        """
    )

    if not rows:
        return {"loaded": 0, "slots": {i: 0 for i in range(5)}}

    loaded = 0
    async with _lock:
        for row in rows:
            token = int(row["instrument_token"])
            if token in _token_to_slot:
                continue
            slot = min(range(5), key=lambda s: len(_active[s]))
            if len(_active[slot]) >= 5000:
                log.warning(
                    "Skipping Tier-A token %s during state rehydrate: all WS slots full",
                    token,
                )
                continue
            _active[slot].add(token)
            _token_to_slot[token] = slot
            loaded += 1

    if loaded:
        slot_breakdown = ", ".join(f"WS-{i}: {len(_active[i])}" for i in range(5))
        log.info(
            "Tier-A startup rehydrate loaded %s tokens from watchlists/open positions. %s",
            loaded,
            slot_breakdown,
        )

    return {"loaded": loaded, "slots": {i: len(_active[i]) for i in range(5)}}


# ── Tier-A on-demand ────────────────────────────────────────────────────────

async def subscribe_tier_a(instrument_token: int) -> int | None:
    """
    Subscribe a Tier-A token on the WS slot with the most free capacity.
    Returns the slot assigned, or None if already subscribed.
    Idempotent — calling twice for the same token is safe.
    """
    async with _lock:
        if instrument_token in _token_to_slot:
            return _token_to_slot[instrument_token]  # already subscribed

        # Pick slot with lowest current count
        slot = min(range(5), key=lambda s: len(_active[s]))
        if len(_active[slot]) >= 5000:
            log.error(
                "All 5 WebSocket slots are at capacity (5,000 each). "
                "Cannot subscribe Tier-A token."
            )
            await add_notification(
                category="live_feed",
                severity="error",
                title="Live feed capacity reached",
                message="All 5 WS slots at capacity (5,000 tokens each). Tier-A token not subscribed.",
                dedupe_key="ws-capacity-reached",
                dedupe_ttl_seconds=600,
            )
            return None

        _active[slot].add(instrument_token)
        _token_to_slot[instrument_token] = slot

    # Trigger live subscription on the running WS
    from app.market_data.websocket_manager import ws_manager
    await ws_manager.subscribe_tokens(slot, [instrument_token])
    log.debug(f"Tier-A token {instrument_token} subscribed on WS-{slot}.")
    return slot


async def unsubscribe_tier_a(instrument_token: int) -> None:
    """
    Unsubscribe a Tier-A token — only if it is expired AND
    has no watchlist entry and no open position.
    """
    if not await _is_safe_to_unsubscribe(instrument_token):
        log.debug(
            f"Token {instrument_token} kept — still in watchlist or has open position."
        )
        return

    async with _lock:
        slot = _token_to_slot.pop(instrument_token, None)
        if slot is not None:
            _active[slot].discard(instrument_token)

    from app.market_data.websocket_manager import ws_manager
    await ws_manager.unsubscribe_tokens(slot, [instrument_token])
    log.info(f"Tier-A token {instrument_token} unsubscribed from WS-{slot}.")


async def _is_safe_to_unsubscribe(instrument_token: int) -> bool:
    """Returns True only if token is expired AND not in any watchlist or position."""
    pool = get_pool()

    # OPTIMIZATION: Single combined query instead of 3 separate DB round-trips
    result = await pool.fetchrow(
        """
        SELECT
            COALESCE(im.expiry_date, '2099-12-31'::date) as expiry_date,
            COALESCE(wi.has_watchlist, false) as in_watchlist,
            COALESCE(pp.has_position, false) as has_position
        FROM instrument_master im
        LEFT JOIN (SELECT 1 as has_watchlist FROM watchlist_items WHERE instrument_token = $1 LIMIT 1) wi ON true
        LEFT JOIN (SELECT 1 as has_position FROM paper_positions WHERE instrument_token = $1 AND quantity != 0 LIMIT 1) pp ON true
        WHERE im.instrument_token = $1
        """,
        instrument_token,
    )

    if not result:
        return False  # Token not found

    # Token is safe to unsubscribe only if:
    # 1. It's expired (expiry_date < today)
    # 2. AND it's not in any watchlist
    # 3. AND it has no open position
    is_expired = result["expiry_date"] <= date.today()
    if not is_expired:
        return False

    if result["in_watchlist"]:
        return False

    if result["has_position"]:
        return False

    return True


# ── Expiry rollover ─────────────────────────────────────────────────────────

async def handle_expiry_rollover() -> None:
    """
    Called daily after market close.
    1. Unsubscribes expired Tier-A tokens (no watchlist / no open position).
    2. Evicts expired Tier-B tokens from _active and sends unsubscribe to Dhan WS.
       Equity cash / ETF instruments have no expiry (expiry_date IS NULL) and are
       never evicted here.
    """
    pool = get_pool()

    # ── Tier-A cleanup ───────────────────────────────────────────────────────
    expired_a = await pool.fetch(
        """
        SELECT DISTINCT ss.instrument_token
        FROM subscription_state ss
        JOIN instrument_master im USING (instrument_token)
        WHERE ss.is_active = TRUE
          AND im.tier = 'A'
          AND im.expiry_date <= CURRENT_DATE
        """
    )
    for row in expired_a:
        await unsubscribe_tier_a(row["instrument_token"])

    # ── Tier-B cleanup ───────────────────────────────────────────────────────
    # Find Tier-B tokens in _active whose expiry has passed.
    from app.market_data.websocket_manager import ws_manager

    async with _lock:
        snapshot = {t: s for t, s in _token_to_slot.items()}

    if not snapshot:
        return

    expired_b_rows = await pool.fetch(
        """
        SELECT instrument_token, ws_slot
        FROM instrument_master
        WHERE tier = 'B'
          AND expiry_date IS NOT NULL
          AND expiry_date < CURRENT_DATE
          AND instrument_token = ANY($1::bigint[])
        """,
        list(snapshot.keys()),
    )

    if not expired_b_rows:
        return

    # Group by slot for efficient batch unsubscribe
    by_slot: dict[int, list[int]] = {}
    for row in expired_b_rows:
        token = row["instrument_token"]
        slot  = row["ws_slot"]
        if slot is not None:
            by_slot.setdefault(slot, []).append(token)

    async with _lock:
        for slot, tokens in by_slot.items():
            for t in tokens:
                _active[slot].discard(t)
                _token_to_slot.pop(t, None)

    for slot, tokens in by_slot.items():
        await ws_manager.unsubscribe_tokens(slot, tokens)

    total_evicted = sum(len(v) for v in by_slot.values())
    log.info(f"Expiry rollover: evicted {total_evicted} expired Tier-B tokens from WS.")


# ── Helpers ─────────────────────────────────────────────────────────────────

def get_slot_tokens(slot: int) -> set[int]:
    """Return all tokens currently assigned to a given WS slot."""
    return _active.get(slot, set()).copy()


def get_all_active_tokens() -> dict[int, set[int]]:
    """Return snapshot of {slot: {tokens}} for all 5 slots."""
    return {s: tokens.copy() for s, tokens in _active.items()}


def slot_count(slot: int) -> int:
    return len(_active.get(slot, set()))


def get_stats() -> dict:
    """Summary of current subscription state — used by Admin Dashboard."""
    return {
        "slots": [
            {"slot": s, "tokens": len(_active[s]), "capacity": 5000}
            for s in range(5)
        ],
        "total_tokens": sum(len(_active[s]) for s in range(5)),
        "tier_a_tokens": sum(
            1 for token, slot in _token_to_slot.items()
            if slot is not None  # rough proxy — can refine with tier lookup
        ),
    }
