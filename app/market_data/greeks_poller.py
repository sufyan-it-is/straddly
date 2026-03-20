"""
app/market_data/greeks_poller.py
==================================
Polling loop — calls POST /optionchain every 15 seconds per active expiry.
Writes Greeks + IV into option_chain_data table (REST skeleton + live hydration).

At startup, also builds the option chain skeleton (all strikes + metadata)
so the frontend always has a complete row structure to query, even before
WebSocket ticks arrive for each token.

Rate: 1 unique call per 3s (DhanHQ limit). Application polls every 15s.
With 12 active expiry slots → 12 calls per 15s cycle = 0.8 req/s — well within limit.
"""
import asyncio
import logging
from datetime import datetime, date
from decimal import Decimal

from app.config                   import get_settings
from app.database                 import get_pool
from app.market_data.rate_limiter import dhan_client
from app.market_data.atm_selector import legs_from_rest_optionchain, select_atm_from_straddle_legs
from app.market_hours             import MarketState, get_market_state, is_equity_window_active
from app.instruments.atm_calculator import update_atm, get_atm, set_atm
from app.market_data.close_price_validator import validate_close_price
from app.market_data.index_underlyings import (
    IDX_SEG as _IDX_SEG,
    IDX_UNDERLYINGS,
    resolve_index_security_id,
)

log = logging.getLogger(__name__)
cfg = get_settings()


# ── ATM DB persistence helpers ──────────────────────────────────────────────

async def load_atm_from_db() -> None:
    """
    Load ATM strikes persisted in system_config into the in-memory cache.
    Call this before build_skeleton() so cold-start uses the last known ATM
    instead of falling back to the middle-of-strikes heuristic.
    """
    pool = get_pool()
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM system_config WHERE key LIKE 'atm_persist_%'"
        )
    except Exception as exc:
        log.warning(f"[ATM] load_atm_from_db query failed: {exc}")
        return

    loaded = []
    for row in rows:
        key = row["key"]
        if not key.startswith("atm_persist_"):
            continue
        underlying = key[len("atm_persist_"):]
        if not underlying:
            continue
        try:
            atm_strike = float(row["value"])
            if atm_strike > 0:
                set_atm(underlying, atm_strike)
                loaded.append(f"{underlying}={atm_strike:.2f}")
        except (ValueError, TypeError):
            continue

    if loaded:
        log.info(f"[ATM] Loaded from DB persistence: {', '.join(loaded)}")
    else:
        log.info("[ATM] No persisted ATM values found in DB — will derive from live data.")


async def persist_atm(underlying: str, atm_strike: float) -> None:
    """Persist ATM strike to system_config so it survives restarts."""
    try:
        pool = get_pool()
        await pool.execute(
            """
            INSERT INTO system_config (key, value, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            f"atm_persist_{underlying.upper()}",
            str(atm_strike),
        )
        log.info(f"[ATM] Persisted {underlying}={atm_strike:.2f} to DB")
    except Exception as exc:
        log.warning(f"[ATM] Persist failed [{underlying}={atm_strike}]: {exc}")


# Strike interval mapping (kept in-sync with /options endpoints)
_STRIKE_INTERVALS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "SENSEX": 100,
    "MIDCPNIFTY": 25,
    "FINNIFTY": 50,
    "BANKEX": 100,
}

_STRIKES_EACH_SIDE = 50  # ATM ± 50 = 101 strikes — wide enough so the true ATM
                          # (from live option premiums) is always within the skeleton
                          # even after large intraday index moves (e.g. BANKNIFTY ±1500pts)


class _GreeksPoller:

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._interval: int = cfg.greeks_poll_seconds
        self._pause_until: float = 0.0  # monotonic timestamp
        # One-time forced poll (even if market is closed) to seed prices/prev_close.
        # Resets to False after the first loop iteration.
        self._force_once: bool = True

    async def start(self) -> None:
        if self.is_running:
            return
        self._task = asyncio.create_task(self._poll_loop(), name="greeks-poller")
        log.info(f"Greeks poller started (interval={self._interval}s).")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def refresh_now(self) -> None:
        """Force the next loop iteration to run even if market is closed."""
        self._force_once = True

    async def set_interval(self, seconds: int) -> None:
        self._interval = seconds

    # ── Skeleton build at startup ───────────────────────────────────────────

    async def build_skeleton(self) -> None:
        """
        Build option_chain_data skeleton from instrument_master (CSV-derived):
          - expiries from instrument_master
          - strikes from instrument_master (ATM-window only)
          - lot size from instrument_master

        Greeks/IV are populated later by the 15s REST poller.
        """
        log.info("Building option chain skeleton from instrument_master…")
        # Prime the in-memory ATM cache from DB before building so that
        # post-redeploy restarts use the last admin-confirmed ATM instead
        # of falling back to the middle-of-strikes heuristic.
        await load_atm_from_db()
        expiry_pairs = await self._get_active_expiry_pairs()
        for underlying, expiry in expiry_pairs:
            try:
                await self._seed_skeleton_from_master(underlying, expiry)
            except Exception as exc:
                log.warning(f"Skeleton seed failed [{underlying} {expiry}]: {exc}")
        log.info("Option chain skeleton seeded.")

    async def _seed_skeleton_from_master(self, underlying: str, expiry: date) -> None:
        """Insert option_chain_data rows (no greeks) for ATM-window strikes."""
        pool = get_pool()
        allowed = await self._allowed_strikes_from_master(underlying, expiry)
        if not allowed:
            return

        rows = await pool.fetch(
            """
            SELECT instrument_token, strike_price, option_type
            FROM instrument_master
            WHERE underlying = $1
              AND instrument_type IN ('OPTIDX','OPTSTK','OPTFUT')
              AND expiry_date = $2::date
              AND strike_price IS NOT NULL
              AND option_type IN ('CE','PE')
            """,
            underlying,
            expiry,
        )

        to_insert = []
        for r in rows:
            sp = float(r["strike_price"])
            if round(sp, 2) not in allowed:
                continue
            to_insert.append((
                int(r["instrument_token"]),
                underlying,
                expiry,
                round(sp, 2),
                r["option_type"],
            ))

        if not to_insert:
            return

        async with pool.acquire() as conn:
            # Remove orphan rows for this expiry whose token is no longer in
            # instrument_master (e.g. after instrument reload with new token IDs).
            # This prevents stale duplicates building up on the logical key.
            await conn.execute(
                """
                DELETE FROM option_chain_data
                WHERE underlying   = $1
                  AND expiry_date  = $2::date
                  AND instrument_token NOT IN (
                      SELECT instrument_token FROM instrument_master
                  )
                """,
                underlying, expiry,
            )
            await conn.executemany(
                """
                INSERT INTO option_chain_data
                    (instrument_token, underlying, expiry_date, strike_price, option_type)
                VALUES ($1,$2,$3::date,$4,$5)
                ON CONFLICT DO NOTHING
                """,
                to_insert,
            )

    async def _allowed_strikes_from_master(self, underlying: str, expiry: date) -> set[float]:
        """Compute allowed strikes = ATM ± N strikes, using market_data LTP + CSV strikes."""
        pool = get_pool()
        underlying = underlying.upper()

        strike_rows = await pool.fetch(
            """
            SELECT DISTINCT strike_price
            FROM instrument_master
            WHERE underlying = $1
              AND instrument_type IN ('OPTIDX','OPTSTK','OPTFUT')
              AND expiry_date = $2::date
              AND strike_price IS NOT NULL
            ORDER BY strike_price
            """,
            underlying,
            expiry,
        )
        strikes = [float(r["strike_price"]) for r in strike_rows]
        if not strikes:
            return set()

        step = float(_STRIKE_INTERVALS.get(underlying) or 0)
        if step <= 0 and len(strikes) >= 2:
            diffs = sorted({round(strikes[i+1] - strikes[i], 2) for i in range(len(strikes) - 1) if strikes[i+1] > strikes[i]})
            step = diffs[0] if diffs else 100.0
        if step <= 0:
            step = 100.0

        # Prefer cached ATM; otherwise derive from live underlying LTP.
        # Do NOT persist midpoint fallback into cache, because that can poison
        # downstream /options/live ATM when market-data LTP is temporarily missing.
        atm = get_atm(underlying)
        if atm is None:
            ltp_row = await pool.fetchrow(
                """
                SELECT md.ltp
                FROM market_data md
                JOIN instrument_master im ON im.instrument_token = md.instrument_token
                WHERE im.symbol = $1 AND im.instrument_type = 'INDEX'
                LIMIT 1
                """,
                underlying,
            )
            ltp = float(ltp_row["ltp"]) if (ltp_row and ltp_row["ltp"]) else None
            if ltp is None:
                # Fallback: pick the middle strike as a stable deterministic centre
                center = strikes[len(strikes) // 2]
                atm = Decimal(str(center))
            else:
                atm = update_atm(underlying, float(ltp), step)
                # Persist so future cold-starts use this LTP-derived ATM
                try:
                    await persist_atm(underlying, float(atm))
                except Exception:
                    pass

        atm_f = float(atm)
        # Find closest available strike to ATM
        closest = min(strikes, key=lambda s: abs(s - atm_f))
        idx = strikes.index(closest)
        lo = max(0, idx - _STRIKES_EACH_SIDE)
        hi = min(len(strikes) - 1, idx + _STRIKES_EACH_SIDE)
        return {round(s, 2) for s in strikes[lo:hi+1]}

    # ── Main poll loop ──────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)

            # Temporary pause (e.g., Dhan 401/429). Avoid hammering.
            now_m = asyncio.get_running_loop().time()
            if now_m < self._pause_until:
                continue

            run_even_if_closed = False
            if self._force_once:
                run_even_if_closed = True
                self._force_once = False

            if not run_even_if_closed and not is_equity_window_active():
                continue   # no point polling when market is closed

            expiry_pairs = await self._get_active_expiry_pairs()
            for underlying, expiry in expiry_pairs:
                # Pause can be set mid-cycle (e.g., first expiry returns 401/429).
                if asyncio.get_running_loop().time() < self._pause_until:
                    break
                try:
                    await self._fetch_and_store(underlying, str(expiry))
                except Exception as exc:
                    log.error(
                        f"Greeks poll error [{underlying} {expiry}]: {exc}"
                    )

    # ── Fetch & store ───────────────────────────────────────────────────────

    async def _fetch_and_store(self, underlying: str, expiry: str) -> None:
        """
        Post to /optionchain via dhan_client — rate limiting is automatic.
        """
        security_id = await resolve_index_security_id(underlying)
        if not security_id:
            return

        expiry_date = date.fromisoformat(expiry)

        resp = await dhan_client.post(
            "/optionchain",
            json={
                "UnderlyingScrip": security_id,
                "UnderlyingSeg":   _IDX_SEG,
                "Expiry":          expiry,
            },
        )
        # If auth/entitlement is invalid (or we're temporarily blocked), pause polling.
        if resp.status_code in (401, 403):
            body = (resp.text or "").lower()
            # 808: bad token/client id pair. Avoid hammering and getting blocked.
            if "808" in body or "authentication failed" in body or "token invalid" in body:
                self._pause_until = asyncio.get_running_loop().time() + 900.0
                log.error(
                    "Greeks poller paused (15m) — Dhan authentication failed (808). "
                    "Fix client_id/access_token in Admin and reconnect."
                )
                return
            # 806: Data APIs not subscribed.
            if "806" in body or "data apis not subscribed" in body:
                self._pause_until = asyncio.get_running_loop().time() + 600.0
                return
            return
        if resp.status_code == 429:
            self._pause_until = asyncio.get_running_loop().time() + 300.0
            return
        if resp.status_code != 200:
            log.warning(
                f"Option chain API returned {resp.status_code} for "
                f"{underlying} {expiry}"
            )
            return

        data = resp.json().get("data", {})
        if not data:
            return

        last_price = data.get("last_price")
        oc         = data.get("oc", {})

        # Keep underlying spot cache aligned to Dhan index spot when available.
        try:
            lp = float(last_price) if last_price is not None else None
            step = float(_STRIKE_INTERVALS.get(underlying) or 0)
            if lp is not None and lp > 0 and step > 0:
                update_atm(underlying, lp, step)
        except Exception:
            pass

        # ATM rule: straddle-min with spot guardrails.
        # Use direct cache set so ATM is not re-derived from rounded underlying LTP.
        try:
            lp = float(last_price) if last_price is not None else None
            step = float(_STRIKE_INTERVALS.get(underlying) or 0)
            legs = legs_from_rest_optionchain(oc)
            best_strike, _atm_meta = select_atm_from_straddle_legs(
                legs,
                spot_price=lp if (lp is not None and lp > 0) else None,
                strike_step=step if step > 0 else None,
            )

            if best_strike is not None:
                set_atm(underlying, best_strike, lp if (lp is not None and lp > 0) else None)
        except Exception:
            pass

        pool = get_pool()
        allowed = await self._allowed_strikes_from_master(underlying, expiry_date)
        
        # Fetch existing prev_close values for validation
        tokens_to_fetch = []
        for strike_str, strikes_data in oc.items():
            for opt_type, opt_data in strikes_data.items():
                sec_id = opt_data.get("security_id")
                if sec_id:
                    tokens_to_fetch.append(int(sec_id))
        
        existing_prev_close = {}
        if tokens_to_fetch:
            existing_rows = await pool.fetch(
                "SELECT instrument_token, prev_close FROM option_chain_data "
                "WHERE instrument_token = ANY($1::bigint[])",
                tokens_to_fetch,
            )
            existing_prev_close = {
                r["instrument_token"]: float(r["prev_close"]) if r["prev_close"] else None
                for r in existing_rows
            }
        
        rows = []
        for strike_str, strikes_data in oc.items():
            try:
                strike = round(float(strike_str), 2)
            except ValueError:
                continue
            if allowed and strike not in allowed:
                continue
            for opt_type, opt_data in strikes_data.items():
                if opt_type not in ("ce", "pe"):
                    continue
                greeks    = opt_data.get("greeks", {})
                sec_id    = opt_data.get("security_id")
                if not sec_id:
                    continue

                sec_id_int = int(sec_id)
                prev_close = opt_data.get("previous_close_price")
                
                # Validate prev_close before storing
                if prev_close is not None:
                    symbol = f"{underlying} {strike}{opt_type.upper()}"
                    existing = existing_prev_close.get(sec_id_int)
                    ltp = opt_data.get("last_price")
                    segment = "BSE_FNO" if underlying in ("SENSEX", "BANKEX") else "NSE_FNO"
                    is_market_active = get_market_state(segment, symbol) == MarketState.OPEN
                    
                    is_valid, reason = validate_close_price(
                        close_price=prev_close,
                        instrument_token=sec_id_int,
                        prev_close=existing,
                        ltp=ltp,
                        is_market_open=is_market_active,
                        symbol=symbol
                    )
                    
                    # If validation fails, skip this prev_close
                    if not is_valid:
                        prev_close = None
                        log.warning(
                            f"[GREEKS] Rejected prev_close for {symbol} "
                            f"({sec_id_int}): {reason}"
                        )

                rows.append((
                    sec_id_int,
                    underlying,
                    expiry_date,
                    strike,
                    opt_type.upper(),
                    opt_data.get("implied_volatility"),
                    greeks.get("delta"),
                    greeks.get("theta"),
                    greeks.get("gamma"),
                    greeks.get("vega"),
                    prev_close,  # Validated prev_close (or None if invalid)
                    opt_data.get("previous_oi"),
                ))

                # Also seed market_data with last_price if not yet present
                # Validate close price here too
                if opt_data.get("last_price") is not None:
                    close_for_market_data = opt_data.get("previous_close_price")
                    seg = "NSE_FNO"
                    if underlying in ("SENSEX", "BANKEX"):
                        seg = "BSE_FNO"
                    
                    # Validate before seeding market_data
                    if close_for_market_data is not None:
                        symbol = f"{underlying} {strike}{opt_type.upper()}"
                        is_market_active = get_market_state(seg, symbol) == MarketState.OPEN
                        is_valid, _ = validate_close_price(
                            close_price=close_for_market_data,
                            instrument_token=sec_id_int,
                            prev_close=None,  # New seed, no previous
                            ltp=opt_data.get("last_price"),
                            is_market_open=is_market_active,
                            symbol=symbol
                        )
                        if not is_valid:
                            close_for_market_data = None
                    
                    await pool.execute(
                        """
                        INSERT INTO market_data (instrument_token, exchange_segment,
                            ltp, close, updated_at)
                        VALUES ($1, $2, $3, $4, now())
                        ON CONFLICT (instrument_token) DO UPDATE SET
                            exchange_segment = EXCLUDED.exchange_segment,
                            ltp = EXCLUDED.ltp,
                            close = COALESCE(EXCLUDED.close, market_data.close),
                            updated_at = now()
                        """,
                        sec_id_int,
                        seg,
                        opt_data["last_price"],
                        close_for_market_data,
                    )

        if not rows:
            return

        async with pool.acquire() as conn:
            # Purge orphan rows before upserting so the new logical-key unique
            # index never conflicts with stale entries from old instrument loads.
            await conn.execute(
                """
                DELETE FROM option_chain_data
                WHERE underlying   = $1
                  AND expiry_date  = $2::date
                  AND instrument_token NOT IN (
                      SELECT instrument_token FROM instrument_master
                  )
                """,
                underlying, expiry_date,
            )
            await conn.executemany(
                """
                INSERT INTO option_chain_data
                    (instrument_token, underlying, expiry_date, strike_price,
                     option_type, iv, delta, theta, gamma, vega,
                     prev_close, prev_oi, greeks_updated_at)
                VALUES ($1,$2,$3::date,$4,$5,$6,$7,$8,$9,$10,$11,$12, now())
                ON CONFLICT (instrument_token) DO UPDATE SET
                    iv                = EXCLUDED.iv,
                    delta             = EXCLUDED.delta,
                    theta             = EXCLUDED.theta,
                    gamma             = EXCLUDED.gamma,
                    vega              = EXCLUDED.vega,
                    prev_close        = EXCLUDED.prev_close,
                    prev_oi           = EXCLUDED.prev_oi,
                    greeks_updated_at = now()
                """,
                rows,
            )
        log.debug(
            f"Greeks updated — {underlying} {expiry}: {len(rows)} strikes."
        )

    # ── Active expiry helper ────────────────────────────────────────────────

    async def _get_active_expiry_pairs(self) -> list[tuple[str, date]]:
        """
        Returns list of (underlying, expiry_date) for all active index option expiries.
        """
        pool = get_pool()
        # Polling every expiry for every underlying can easily exceed Dhan limits.
        # We only poll the nearest N expiries per underlying; the skeleton still
        # exists for all strikes/expiries.
        rows = await pool.fetch(
            """
            WITH ranked AS (
                SELECT
                    underlying,
                    expiry_date,
                    ROW_NUMBER() OVER (PARTITION BY underlying ORDER BY expiry_date) AS rn
                FROM instrument_master
                WHERE instrument_type = 'OPTIDX'
                  AND underlying = ANY($1::text[])
                  AND expiry_date >= CURRENT_DATE
                GROUP BY underlying, expiry_date
            )
            SELECT underlying, expiry_date
            FROM ranked
            WHERE rn <= 2
            ORDER BY underlying, expiry_date
            """,
            list(IDX_UNDERLYINGS),
        )
        return [(r["underlying"], r["expiry_date"]) for r in rows]


# Singleton
greeks_poller = _GreeksPoller()
