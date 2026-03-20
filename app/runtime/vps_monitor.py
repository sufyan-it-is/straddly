"""
Lightweight in-process VPS monitor for Super Admin dashboard.

This monitor is intentionally manual: it only runs when started via API
and stops when requested or when app shuts down.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional


class VPSMonitor:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False
        self._interval_seconds: int = 5
        self._history: Deque[Dict] = deque(maxlen=1440)  # ~2h at 5s
        self._started_at: Optional[datetime] = None

        # For CPU percentage deltas
        self._prev_cpu_total: Optional[int] = None
        self._prev_cpu_idle: Optional[int] = None
        self._prev_proc_time: Optional[float] = None
        self._prev_wall: Optional[float] = None

    async def start(self, interval_seconds: int = 5) -> Dict:
        if self._running:
            return self.status()

        self._interval_seconds = max(2, min(int(interval_seconds), 60))
        self._running = True
        self._started_at = datetime.now(timezone.utc)

        # Reset deltas to avoid a large first-spike due to stale counters.
        self._prev_cpu_total = None
        self._prev_cpu_idle = None
        self._prev_proc_time = None
        self._prev_wall = None

        self._task = asyncio.create_task(self._run(), name="vps-monitor-loop")
        return self.status()

    async def stop(self) -> Dict:
        self._running = False
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return self.status()

    def status(self) -> Dict:
        last_ts = self._history[-1]["timestamp"] if self._history else None
        return {
            "running": self._running,
            "interval_seconds": self._interval_seconds,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_sample_at": last_ts,
            "sample_count": len(self._history),
        }

    def samples(self, limit: int = 120) -> Dict:
        safe_limit = max(1, min(int(limit), 1000))
        return {
            "running": self._running,
            "samples": list(self._history)[-safe_limit:],
        }

    async def _run(self) -> None:
        try:
            while self._running:
                sample = self._collect_sample()
                self._history.append(sample)
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            raise

    def _collect_sample(self) -> Dict:
        now = datetime.now(timezone.utc)

        cpu_pct = self._read_system_cpu_percent()
        mem = self._read_memory()
        proc_cpu = self._read_process_cpu_percent()

        load_1m, load_5m, load_15m = self._read_loadavg()

        return {
            "timestamp": now.isoformat(),
            "system_cpu_percent": round(cpu_pct, 2),
            "app_cpu_percent": round(proc_cpu, 2),
            "memory_used_percent": round(mem["used_percent"], 2),
            "memory_used_mb": round(mem["used_mb"], 2),
            "memory_total_mb": round(mem["total_mb"], 2),
            "load_1m": round(load_1m, 2),
            "load_5m": round(load_5m, 2),
            "load_15m": round(load_15m, 2),
        }

    def _read_system_cpu_percent(self) -> float:
        try:
            with open("/proc/stat", "r", encoding="utf-8") as f:
                line = f.readline().strip()
            parts = line.split()
            if len(parts) < 5 or parts[0] != "cpu":
                return 0.0

            values = [int(v) for v in parts[1:]]
            total = sum(values)
            idle = values[3] + (values[4] if len(values) > 4 else 0)

            if self._prev_cpu_total is None or self._prev_cpu_idle is None:
                self._prev_cpu_total = total
                self._prev_cpu_idle = idle
                return 0.0

            delta_total = total - self._prev_cpu_total
            delta_idle = idle - self._prev_cpu_idle
            self._prev_cpu_total = total
            self._prev_cpu_idle = idle

            if delta_total <= 0:
                return 0.0
            return max(0.0, min(100.0, (1.0 - (delta_idle / delta_total)) * 100.0))
        except Exception:
            return 0.0

    def _read_memory(self) -> Dict[str, float]:
        total_kb = 0.0
        available_kb = 0.0
        try:
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_kb = float(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        available_kb = float(line.split()[1])
            if total_kb <= 0:
                return {"used_percent": 0.0, "used_mb": 0.0, "total_mb": 0.0}
            used_kb = max(0.0, total_kb - available_kb)
            return {
                "used_percent": (used_kb / total_kb) * 100.0,
                "used_mb": used_kb / 1024.0,
                "total_mb": total_kb / 1024.0,
            }
        except Exception:
            return {"used_percent": 0.0, "used_mb": 0.0, "total_mb": 0.0}

    def _read_loadavg(self) -> tuple[float, float, float]:
        try:
            with open("/proc/loadavg", "r", encoding="utf-8") as f:
                parts = f.read().strip().split()
            if len(parts) < 3:
                return (0.0, 0.0, 0.0)
            return (float(parts[0]), float(parts[1]), float(parts[2]))
        except Exception:
            return (0.0, 0.0, 0.0)

    def _read_process_cpu_percent(self) -> float:
        try:
            with open("/proc/self/stat", "r", encoding="utf-8") as f:
                stat = f.read().strip().split()
            if len(stat) < 17:
                return 0.0

            utime = float(stat[13])
            stime = float(stat[14])
            proc_time_ticks = utime + stime

            # Linux defaults to 100 clock ticks/second for /proc stat counters.
            hz = 100.0
            proc_time_sec = proc_time_ticks / hz
            wall_now = asyncio.get_running_loop().time()

            if self._prev_proc_time is None or self._prev_wall is None:
                self._prev_proc_time = proc_time_sec
                self._prev_wall = wall_now
                return 0.0

            dt_proc = proc_time_sec - self._prev_proc_time
            dt_wall = wall_now - self._prev_wall
            self._prev_proc_time = proc_time_sec
            self._prev_wall = wall_now

            if dt_wall <= 0:
                return 0.0

            cpu_count = max(1, os.cpu_count() or 1)
            pct = (dt_proc / dt_wall) * 100.0 / cpu_count
            return max(0.0, min(100.0, pct))
        except Exception:
            return 0.0


vps_monitor = VPSMonitor()
