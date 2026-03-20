"""
app/routers/watchlist.py  (v2 — simplified flat API matching frontend)
GET  /watchlist/{user_id}          → [{symbol, token, exchange, ...}]
POST /watchlist/add                → {user_id, token, symbol, exchange}
POST /watchlist/remove             → {user_id, token}
"""
import logging
import uuid
from typing import Optional
import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.database import get_pool
from app.dependencies import CurrentUser
import app.instruments.subscription_manager as subscription_manager


log = logging.getLogger(__name__)

router = APIRouter(prefix="/watchlist", tags=["Watchlist"])


def _depth_top_price(depth) -> float | None:
    """Return first level price from bid/ask depth json, if available."""
    if depth is None:
        return None
    if isinstance(depth, str):
        try:
            depth = json.loads(depth)
        except Exception:
            return None
    if not isinstance(depth, list) or not depth:
        return None
    first = depth[0]
    if not isinstance(first, dict):
        return None
    price = first.get("price")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def _normalise_depth_levels(depth, max_levels: int = 5) -> list[dict]:
    """Normalise depth JSON into [{price, qty}] and cap to max_levels."""
    if depth is None:
        return []
    if isinstance(depth, str):
        try:
            depth = json.loads(depth)
        except Exception:
            return []
    if not isinstance(depth, list):
        return []

    out: list[dict] = []
    for level in depth[:max_levels]:
        if not isinstance(level, dict):
            continue
        try:
            price = float(level.get("price")) if level.get("price") is not None else None
        except (TypeError, ValueError):
            price = None
        if price is None:
            continue
        try:
            qty = int(level.get("qty")) if level.get("qty") is not None else 0
        except (TypeError, ValueError):
            qty = 0
        out.append({"price": price, "qty": qty})
    return out


def _uid(request: Request, user_id_param, current_user: Optional[CurrentUser] = None) -> str:
    if user_id_param:
        return str(user_id_param)
    hdr = request.headers.get("X-USER")
    if hdr:
        return hdr
    if current_user:
        return str(current_user.id)
    raise HTTPException(status_code=401, detail="Authentication required")


def _require_uuid(uid: str) -> str:
    try:
        return str(uuid.UUID(str(uid)))
    except Exception:
        raise HTTPException(status_code=422, detail="user_id must be a UUID")


class AddItemRequest(BaseModel):
    user_id:  Optional[str] = None
    token:    Optional[str] = None       # instrument token (string or int)
    symbol:   Optional[str] = None
    exchange: Optional[str] = None


class RemoveItemRequest(BaseModel):
    user_id: Optional[str] = None
    token:   Optional[str] = None


class ReorderWatchlistRequest(BaseModel):
    user_id: Optional[str] = None
    tokens: list[str] = []


async def _resolve_token_from_db(pool, token_val: Optional[int], symbol: str) -> Optional[int]:
    if token_val:
        row = await pool.fetchrow(
            """
            SELECT instrument_token
            FROM instrument_master
            WHERE instrument_token = $1
               OR security_id = $1
            LIMIT 1
            """,
            int(token_val),
        )
        if row:
            return int(row["instrument_token"])

    if symbol:
        try:
            row = await pool.fetchrow(
                """
                SELECT instrument_token
                FROM instrument_master
                WHERE upper(symbol) = upper($1)
                   OR upper(COALESCE(trading_symbol, '')) = upper($1)
                   OR upper(COALESCE(display_name, '')) = upper($1)
                   OR upper(COALESCE(underlying, '')) = upper($1)
                   OR symbol ILIKE $2
                   OR COALESCE(trading_symbol, '') ILIKE $2
                   OR COALESCE(display_name, '') ILIKE $2
                   OR COALESCE(underlying, '') ILIKE $2
                ORDER BY
                    CASE
                        WHEN upper(symbol) = upper($1) THEN 0
                        WHEN upper(COALESCE(trading_symbol, '')) = upper($1) THEN 0
                        WHEN upper(COALESCE(display_name, '')) = upper($1) THEN 1
                        WHEN upper(COALESCE(underlying, '')) = upper($1) THEN 1
                        ELSE 2
                    END,
                    instrument_token
                LIMIT 1
                """,
                symbol,
                f"%{symbol}%",
            )
        except Exception:
            # Backward-compatible fallback for schemas without trading_symbol column.
            row = await pool.fetchrow(
                """
                SELECT instrument_token
                FROM instrument_master
                WHERE upper(symbol) = upper($1)
                   OR upper(COALESCE(display_name, '')) = upper($1)
                   OR upper(COALESCE(underlying, '')) = upper($1)
                   OR symbol ILIKE $2
                   OR COALESCE(display_name, '') ILIKE $2
                   OR COALESCE(underlying, '') ILIKE $2
                ORDER BY
                    CASE
                        WHEN upper(symbol) = upper($1) THEN 0
                        WHEN upper(COALESCE(display_name, '')) = upper($1) THEN 1
                        WHEN upper(COALESCE(underlying, '')) = upper($1) THEN 1
                        ELSE 2
                    END,
                    instrument_token
                LIMIT 1
                """,
                symbol,
                f"%{symbol}%",
            )
        if row:
            return int(row["instrument_token"])

    return None


async def _resolve_token_with_csv_fallback(pool, token_val: Optional[int], symbol: str) -> Optional[int]:
    resolved = await _resolve_token_from_db(pool, token_val, symbol)
    if resolved:
        return resolved

    # If not found in DB, refresh instrument_master from local CSV and retry once.
    try:
        from app.instruments.scrip_master import refresh_instruments
        await refresh_instruments(download=False)
    except Exception as exc:
        log.warning("CSV fallback refresh failed while resolving watchlist token: %s", exc)

    return await _resolve_token_from_db(pool, token_val, symbol)


async def _repair_zero_token_rows(pool, watchlist_id: str) -> None:
    rows = await pool.fetch(
        """
        SELECT instrument_token, symbol
        FROM watchlist_items
        WHERE watchlist_id = $1 AND instrument_token = 0
        """,
        watchlist_id,
    )
    if not rows:
        return

    repaired = 0
    for row in rows:
        symbol = (row.get("symbol") or "").strip()
        if not symbol:
            continue
        resolved = await _resolve_token_with_csv_fallback(pool, None, symbol)
        if not resolved:
            continue
        try:
            await pool.execute(
                "DELETE FROM watchlist_items WHERE watchlist_id=$1 AND instrument_token=$2",
                watchlist_id,
                resolved,
            )
            await pool.execute(
                """
                UPDATE watchlist_items
                SET instrument_token = $3
                WHERE watchlist_id = $1 AND instrument_token = 0 AND symbol = $2
                """,
                watchlist_id,
                symbol,
                resolved,
            )
            repaired += 1
        except Exception:
            continue

    if repaired:
        log.info("Repaired %s legacy watchlist rows with token=0 for watchlist %s", repaired, watchlist_id)


@router.get("/{user_id}")
async def get_watchlist(user_id: str, request: Request):
    """Return flat list of all watchlist instruments for a user.

    Tier-B items are always returned.
    Tier-A items are returned as-is; stale entries are removed once daily
    at 06:30 IST by the WatchlistCleanupScheduler.
    """
    user_id = _require_uuid(user_id)
    pool = get_pool()

    # Find or create a default watchlist for the user
    wl = await pool.fetchrow(
        "SELECT watchlist_id FROM watchlists WHERE user_id=$1 LIMIT 1", user_id
    )

    if not wl:
        # No watchlist yet — return empty list
        return {"data": []}

    await _repair_zero_token_rows(pool, wl["watchlist_id"])

    # Fetch watchlist items with tier and position info
    rows = await pool.fetch(
        """
        SELECT wi.instrument_token AS token,
               CASE
                   WHEN im.instrument_type = 'EQUITY'
                        AND im.underlying IS NOT NULL
                        AND im.underlying <> '' THEN im.underlying
                   ELSE COALESCE(wi.symbol, im.symbol)
               END AS symbol,
               COALESCE(im.exchange_segment, 'NSE') AS exchange,
               COALESCE(im.instrument_type, 'EQ') AS instrument_type,
               im.underlying,
               im.expiry_date,
               im.strike_price,
               im.option_type,
               im.lot_size,
               im.tier,
               md.ltp,
               md.close,
               md.bid_depth,
               md.ask_depth,
               wi.added_at,
               CASE WHEN EXISTS(
                   SELECT 1 FROM paper_positions pp 
                   WHERE pp.instrument_token = wi.instrument_token 
                   AND pp.quantity != 0
               ) THEN true ELSE false END as has_position
        FROM watchlist_items wi
        LEFT JOIN instrument_master im ON im.instrument_token = wi.instrument_token
        LEFT JOIN market_data md ON md.instrument_token = wi.instrument_token
        WHERE wi.watchlist_id = $1
        ORDER BY COALESCE(wi.sort_index, 2147483647) ASC, wi.added_at DESC
        """,
        wl["watchlist_id"],
    )

    result = []
    for r in rows:
        item = dict(r)
        item["id"]    = str(item["token"])
        item["ltp"]   = float(item["ltp"]) if item.get("ltp") else None
        item["close"] = float(item["close"]) if item.get("close") else None
        if item.get("expiry_date"):
            item["expiry_date"] = str(item["expiry_date"])
        item["strike_price"] = float(item["strike_price"]) if item.get("strike_price") is not None else None
        item["lot_size"] = int(item["lot_size"]) if item.get("lot_size") is not None else 1
        item["bid_depth"] = _normalise_depth_levels(item.get("bid_depth"), max_levels=5)
        item["ask_depth"] = _normalise_depth_levels(item.get("ask_depth"), max_levels=5)
        item["best_bid"] = _depth_top_price(item.get("bid_depth"))
        item["best_ask"] = _depth_top_price(item.get("ask_depth"))
        item["tier"] = item.get("tier") or "B"
        item["added_at"] = item["added_at"].isoformat() if item.get("added_at") else None
        item["has_position"] = bool(item.get("has_position"))
        result.append(item)

    return {"data": result}


@router.post("/add")
async def add_to_watchlist(
    body: AddItemRequest,
    request: Request,
):
    uid  = _require_uuid(_uid(request, body.user_id))
    pool = get_pool()

    token_val = int(body.token) if body.token and str(body.token).isdigit() else None
    symbol    = (body.symbol or "").strip()
    exchange  = (body.exchange or "NSE").strip().upper()

    token_val = await _resolve_token_with_csv_fallback(pool, token_val, symbol)
    if not token_val:
        # Keep API non-breaking; report unresolved but avoid inserting token=0.
        return {"success": False, "token": None, "symbol": symbol, "detail": "Instrument not found in instrument_master/CSV"}

    # Ensure user has a watchlist
    wl = await pool.fetchrow(
        "SELECT watchlist_id FROM watchlists WHERE user_id=$1 LIMIT 1", uid
    )
    if not wl:
        wl = await pool.fetchrow(
            "INSERT INTO watchlists (user_id, name) VALUES ($1, 'Watchlist 1') RETURNING watchlist_id",
            uid,
        )

    wl_id = wl["watchlist_id"]

    await pool.execute(
        """
        INSERT INTO watchlist_items (watchlist_id, instrument_token, symbol, sort_index)
        VALUES (
            $1,
            $2,
            $3,
            COALESCE((SELECT MAX(sort_index) + 1 FROM watchlist_items WHERE watchlist_id = $1), 0)
        )
        ON CONFLICT (watchlist_id, instrument_token) DO NOTHING
        """,
        wl_id, token_val, symbol,
    )

    # Subscribe Tier-A if applicable
    if token_val:
        try:
            await subscription_manager.subscribe_tier_a(token_val)
        except Exception:
            pass

    return {"success": True, "token": token_val, "symbol": symbol}


@router.post("/remove")
async def remove_from_watchlist(
    body: RemoveItemRequest,
    request: Request,
):
    uid  = _require_uuid(_uid(request, body.user_id))
    pool = get_pool()

    token_val = int(body.token) if body.token and str(body.token).isdigit() else None

    wl = await pool.fetchrow(
        "SELECT watchlist_id FROM watchlists WHERE user_id=$1 LIMIT 1", uid
    )
    if not wl:
        return {"success": True}

    await pool.execute(
        "DELETE FROM watchlist_items WHERE watchlist_id=$1 AND instrument_token=$2",
        wl["watchlist_id"], token_val or 0,
    )

    if token_val:
        try:
            await subscription_manager.unsubscribe_tier_a(token_val)
        except Exception:
            pass

    return {"success": True}


@router.post("/reorder")
async def reorder_watchlist(
    body: ReorderWatchlistRequest,
    request: Request,
):
    uid = _require_uuid(_uid(request, body.user_id))
    pool = get_pool()

    raw_tokens = body.tokens or []
    ordered_tokens: list[int] = []
    seen: set[int] = set()

    for t in raw_tokens:
        raw = str(t or "").strip()
        if not raw.isdigit():
            continue
        token = int(raw)
        if token <= 0 or token in seen:
            continue
        seen.add(token)
        ordered_tokens.append(token)

    wl = await pool.fetchrow(
        "SELECT watchlist_id FROM watchlists WHERE user_id=$1 LIMIT 1", uid
    )
    if not wl:
        return {"success": True, "updated": 0}

    wl_id = wl["watchlist_id"]

    if not ordered_tokens:
        return {"success": True, "updated": 0}

    existing = await pool.fetch(
        "SELECT instrument_token FROM watchlist_items WHERE watchlist_id = $1",
        wl_id,
    )
    existing_tokens = {int(r["instrument_token"]) for r in existing}
    final_order = [t for t in ordered_tokens if t in existing_tokens]

    if not final_order:
        return {"success": True, "updated": 0}

    async with pool.acquire() as conn:
        async with conn.transaction():
            for idx, token in enumerate(final_order):
                await conn.execute(
                    """
                    UPDATE watchlist_items
                    SET sort_index = $3
                    WHERE watchlist_id = $1 AND instrument_token = $2
                    """,
                    wl_id,
                    token,
                    idx,
                )

    return {"success": True, "updated": len(final_order)}
