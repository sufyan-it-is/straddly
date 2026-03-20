import asyncio
import logging

from app.config import get_settings
from app.database import get_pool
from app.market_hours import is_nse_bse_ws_window_open_strict
from app.market_data.depth_ws_manager import depth_ws_manager
from app.market_data.websocket_manager import ws_manager

log = logging.getLogger(__name__)


class DhanWsWindowScheduler:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._last_window_open: bool | None = None
        self._poll_interval_sec = 30.0

    async def start(self) -> None:
        if self._task and not self._task.done():
            log.warning("DhanWsWindowScheduler already running")
            return
        self._task = asyncio.create_task(self._run_loop(), name="dhan_ws_window_scheduler")
        log.info("DhanWsWindowScheduler started.")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("DhanWsWindowScheduler stopped.")

    async def _run_loop(self) -> None:
        while True:
            try:
                window_open = is_nse_bse_ws_window_open_strict()
                if self._last_window_open is None or window_open != self._last_window_open:
                    if window_open:
                        log.info("DhanWsWindowScheduler: NSE/BSE market window opened; ensuring websocket streams are running.")
                    else:
                        log.info("DhanWsWindowScheduler: NSE/BSE market window closed; stopping websocket streams.")
                    self._last_window_open = window_open

                if window_open:
                    await self._ensure_streams_running()
                else:
                    await self._ensure_streams_stopped()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("DhanWsWindowScheduler loop error: %s", exc, exc_info=True)

            await asyncio.sleep(self._poll_interval_sec)

    async def _ensure_streams_running(self) -> None:
        await ws_manager.start_all()

        if depth_ws_manager._task and not depth_ws_manager._task.done():
            return

        cfg = get_settings()
        pool = get_pool()
        depth_rows = await pool.fetch(
            """
            SELECT instrument_token FROM instrument_master
            WHERE underlying = ANY($1::text[])
              AND instrument_type IN ('FUTIDX','OPTIDX')
            LIMIT 10
            """,
            cfg.depth_20_underlying,
        )
        depth_tokens = [row["instrument_token"] for row in depth_rows]
        await depth_ws_manager.start(depth_tokens)

    async def _ensure_streams_stopped(self) -> None:
        await ws_manager.stop_all()
        await depth_ws_manager.stop()


dhan_ws_window_scheduler = DhanWsWindowScheduler()