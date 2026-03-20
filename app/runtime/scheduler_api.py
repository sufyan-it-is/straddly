"""app/runtime/scheduler_api.py

Unified scheduler registry + control helpers for the Super Admin dashboard.

This is intentionally lightweight (in-memory; no persistence).
"""

from __future__ import annotations

from datetime import datetime

from app.market_hours import IST
from app.runtime.market_timing import market_timing_controller


async def get_scheduler_snapshot() -> dict:
    from app.market_data.tick_processor import tick_processor
    from app.market_data.greeks_poller import greeks_poller
    from app.credentials.token_refresher import token_refresher
    from app.execution_simulator import super_order_monitor

    from app.instruments.scrip_master import scrip_scheduler
    from app.margin.nse_margin_data import nse_margin_scheduler
    from app.margin.mcx_margin_data import mcx_margin_scheduler
    from app.positions.eod_archiver import eod_closed_position_archiver
    from app.schedulers.mis_auto_squareoff import mis_auto_squareoff

    from app.market_hours import is_equity_window_active, is_commodity_window_active
    from app.config import get_settings

    now = datetime.now(tz=IST)
    equity_on = is_equity_window_active(now)
    comm_on = is_commodity_window_active(now)
    cfg = get_settings()

    items = [
        {
            "id": "tick_processor",
            "label": "Tick flush / UPSERT",
            "kind": "interval",
            "window": "Equity 09:00–15:30, Commodity 09:00–23:30 (IST)",
            "running": tick_processor.is_running,
            "override": market_timing_controller.get_override("tick_processor"),
            "actions": ["start", "stop", "refresh", "auto"],
            "details": {"batch_ms": getattr(cfg, "tick_batch_ms", 100)},
        },
        {
            "id": "greeks_poller",
            "label": "Greeks poller",
            "kind": "interval",
            "window": "Equity 09:00–15:30 (IST)",
            "running": greeks_poller.is_running,
            "override": market_timing_controller.get_override("greeks_poller"),
            "actions": ["start", "stop", "refresh", "auto"],
            "details": {"interval_s": getattr(greeks_poller, "_interval", None)},
        },
        {
            "id": "super_order_monitor",
            "label": "Super-order monitor",
            "kind": "interval",
            "window": "Equity/Commodity windows (IST)",
            "running": super_order_monitor.is_running(),
            "override": market_timing_controller.get_override("super_order_monitor"),
            "actions": ["start", "stop", "refresh", "auto"],
            "details": {"interval_s": 1},
        },
        {
            "id": "token_refresher",
            "label": "TOTP token refresher",
            "kind": "interval",
            "window": "Equity/Commodity windows (IST)",
            "running": token_refresher.is_running,
            "override": market_timing_controller.get_override("token_refresher"),
            "actions": ["start", "stop", "refresh", "auto"],
            "details": {
                "mode": getattr(token_refresher, "effective_mode", "unknown"),
                "check_minutes": 30,
                "refresh_ahead_minutes": 120,
            },
        },
        {
            "id": "scrip_master_scheduler",
            "label": "Scrip master scheduler",
            "kind": "fixed_time",
            "window": "06:00 IST daily",
            "running": bool(getattr(scrip_scheduler, "_task", None) and not scrip_scheduler._task.done()),
            "override": "n/a",
            "actions": ["start", "stop", "refresh"],
            "details": {
                "last_run_at": scrip_scheduler.last_run_at.isoformat() if scrip_scheduler.last_run_at else None,
                "last_error": scrip_scheduler.last_run_error,
            },
        },
        {
            "id": "nse_margin_scheduler",
            "label": "NSE margin scheduler",
            "kind": "fixed_time",
            "window": "08:45 IST daily (with retries)",
            "running": bool(getattr(nse_margin_scheduler, "_task", None) and not nse_margin_scheduler._task.done()),
            "override": "n/a",
            "actions": ["start", "stop", "refresh"],
            "details": {
                "last_run_at": nse_margin_scheduler.last_run_at.isoformat() if nse_margin_scheduler.last_run_at else None,
                "last_run_success": nse_margin_scheduler.last_run_success,
                "last_error": nse_margin_scheduler.last_run_error,
            },
        },
        {
            "id": "mcx_margin_scheduler",
            "label": "MCX margin scheduler",
            "kind": "fixed_time",
            "window": "08:45 IST daily (with retries)",
            "running": bool(getattr(mcx_margin_scheduler, "_task", None) and not mcx_margin_scheduler._task.done()),
            "override": "n/a",
            "actions": ["start", "stop", "refresh"],
            "details": {
                "last_run_at": mcx_margin_scheduler.last_run_at.isoformat() if mcx_margin_scheduler.last_run_at else None,
                "last_run_success": mcx_margin_scheduler.last_run_success,
                "last_error": mcx_margin_scheduler.last_run_error,
            },
        },
        {
            "id": "eod_closed_position_archiver",
            "label": "EOD closed-position archiver",
            "kind": "fixed_time",
            "window": "16:00 IST daily",
            "running": bool(getattr(eod_closed_position_archiver, "_task", None) and not eod_closed_position_archiver._task.done()),
            "override": "n/a",
            "actions": ["start", "stop", "refresh"],
            "details": {
                "last_run_at": eod_closed_position_archiver.last_run_at.isoformat() if eod_closed_position_archiver.last_run_at else None,
                "last_archived_positions": eod_closed_position_archiver.last_run_result.archived_positions if eod_closed_position_archiver.last_run_result else None,
                "last_archived_orders": eod_closed_position_archiver.last_run_result.archived_orders if eod_closed_position_archiver.last_run_result else None,
                "last_error": eod_closed_position_archiver.last_run_error,
            },
        },
        {
            "id": "mis_auto_squareoff",
            "label": "MIS auto-square-off",
            "kind": "fixed_time",
            "window": "15:20-15:30 IST pulse (NSE/BSE)",
            "running": bool(getattr(mis_auto_squareoff, "_task", None) and not mis_auto_squareoff._task.done()),
            "override": "n/a",
            "actions": ["start", "stop", "refresh"],
            "details": {
                "last_run_at": mis_auto_squareoff.last_run_at.isoformat() if mis_auto_squareoff.last_run_at else None,
                "positions_closed": mis_auto_squareoff.last_run_result.get("positions_closed") if mis_auto_squareoff.last_run_result else None,
                "last_error": mis_auto_squareoff.last_run_error,
            },
        },
        {
            "id": "holidays",
            "label": "Exchange holidays (loaded)",
            "kind": "data",
            "window": "n/a",
            "running": True,
            "override": "n/a",
            "actions": ["refresh"],
            "details": {},
        },
    ]

    # Holiday counts (in-memory sets)
    from app.market_hours import _EXCHANGE_HOLIDAYS  # module-level cache

    holidays = {
        ex: {"count": len(ds)} for ex, ds in (_EXCHANGE_HOLIDAYS or {}).items()
    }

    return {
        "server_time_ist": now.isoformat(),
        "equity_window_active": equity_on,
        "commodity_window_active": comm_on,
        "items": items,
        "holidays": holidays,
    }


async def scheduler_action(name: str, action: str) -> dict:
    from app.market_data.tick_processor import tick_processor
    from app.market_data.greeks_poller import greeks_poller
    from app.credentials.token_refresher import token_refresher
    from app.execution_simulator import super_order_monitor

    from app.instruments.scrip_master import refresh_instruments, scrip_scheduler
    from app.margin.nse_margin_data import download_and_refresh, nse_margin_scheduler
    from app.margin.mcx_margin_data import download_and_refresh as mcx_download_and_refresh, mcx_margin_scheduler
    from app.positions.eod_archiver import eod_closed_position_archiver
    from app.schedulers.mis_auto_squareoff import mis_auto_squareoff
    from app.schedulers.mis_auto_squareoff import mis_auto_squareoff

    if action == "auto":
        market_timing_controller.set_override(name, "auto")
        await market_timing_controller.enforce_now()
        return {"success": True}

    if name in ("tick_processor", "greeks_poller", "super_order_monitor", "token_refresher"):
        if action == "start":
            market_timing_controller.set_override(name, "forced_running")
            await market_timing_controller.enforce_now()
            return {"success": True}
        if action == "stop":
            market_timing_controller.set_override(name, "forced_stopped")
            await market_timing_controller.enforce_now()
            return {"success": True}
        if action == "refresh":
            if name == "tick_processor":
                await tick_processor.flush_now()
            elif name == "greeks_poller":
                await greeks_poller.refresh_now()
            elif name == "super_order_monitor":
                await super_order_monitor.run_once()
            elif name == "token_refresher":
                return await token_refresher.refresh_now()
            return {"success": True}

    if name == "scrip_master_scheduler":
        if action == "start":
            await scrip_scheduler.start()
        elif action == "stop":
            await scrip_scheduler.stop()
        elif action == "refresh":
            await refresh_instruments(download=True)
        return {"success": True}

    if name == "nse_margin_scheduler":
        if action == "start":
            await nse_margin_scheduler.start()
        elif action == "stop":
            await nse_margin_scheduler.stop()
        elif action == "refresh":
            ok = await download_and_refresh()
            return {"success": bool(ok)}
        return {"success": True}

    if name == "mcx_margin_scheduler":
        if action == "start":
            await mcx_margin_scheduler.start()
        elif action == "stop":
            await mcx_margin_scheduler.stop()
        elif action == "refresh":
            result = await mcx_download_and_refresh()
            return {"success": bool(result.get("success")), "symbol_count": result.get("symbol_count")}
        return {"success": True}

    if name == "eod_closed_position_archiver":
        if action == "start":
            await eod_closed_position_archiver.start()
        elif action == "stop":
            await eod_closed_position_archiver.stop()
        elif action == "refresh":
            res = await eod_closed_position_archiver.run_once()
            return {"success": True, "archived_count": res.archived_count}
        return {"success": True}

    if name == "mis_auto_squareoff":
        if action == "start":
            await mis_auto_squareoff.start()
        elif action == "stop":
            await mis_auto_squareoff.stop()
        elif action == "refresh":
            res = await mis_auto_squareoff.run_once()
            return {"success": True, "result": res}
        return {"success": True}

    if name == "holidays" and action == "refresh":
        from app.margin.exchange_holidays import sync_exchange_holidays
        from app.market_hours import load_exchange_holidays_from_db

        await sync_exchange_holidays()
        await load_exchange_holidays_from_db()
        return {"success": True}

    return {"success": False, "detail": "Unknown scheduler/action"}
