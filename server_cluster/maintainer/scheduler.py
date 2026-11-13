"""server_cluster.maintainer.scheduler — the daily maintenance fire.

The repo's ``Event.wait`` daemon-thread pattern
(``monitor.scheduler.DailyBringUp`` shape): sleeps until the next fire
time (Asia/Shanghai, default 03:30), runs every registered
:class:`~server_cluster.maintainer.jobs.MaintenanceJob` once, and
re-arms for the next day. A failed job is that job's ``ok=False``
result — never fatal to the server.

``MAINTAINER_FIRE_AT`` (env, ``HH:MM`` China time) overrides the fire
time for testing; the default stays 03:30 (before the 07:00 bring-up
and the day's pipelines).
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

CHINA_TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_FIRE_AT = "03:30"


def _fire_at_hour_minute() -> tuple[int, int]:
    raw = os.environ.get("MAINTAINER_FIRE_AT", DEFAULT_FIRE_AT)
    try:
        hh, mm = raw.split(":")
        return int(hh), int(mm)
    except ValueError:
        logger.warning("MAINTAINER_FIRE_AT=%r unreadable — using %s",
                       raw, DEFAULT_FIRE_AT)
        hh, mm = DEFAULT_FIRE_AT.split(":")
        return int(hh), int(mm)


def _seconds_to_next_fire(now: datetime | None = None) -> float:
    """Seconds until the next HH:MM China-time fire (today's if it has
    not passed yet, otherwise tomorrow's)."""
    hh, mm = _fire_at_hour_minute()
    now = now or datetime.now(CHINA_TZ)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


class MaintenanceScheduler:
    """The once-a-day pass over the registered jobs (one thread per
    maintainer server)."""

    def __init__(self, jobs: list) -> None:
        self._jobs = jobs
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="cluster-maintainer-daily",
            daemon=True)
        self._thread.start()
        hh, mm = _fire_at_hour_minute()
        logger.info("maintenance armed: next fire at %02d:%02d "
                    "Asia/Shanghai for [%s] (in %.0f s)", hh, mm,
                    ", ".join(j.name for j in self._jobs),
                    _seconds_to_next_fire())

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def run_all(self) -> list:
        """One pass over every scheduled job now (the daily fire and the
        API's run-on-demand share this; jobs with a dedicated thread —
        ``scheduled=False`` — are skipped here)."""
        return [job.run() for job in self._jobs if job.scheduled]

    def _loop(self) -> None:
        while not self._stop.wait(_seconds_to_next_fire()):
            try:
                for result in self.run_all():
                    logger.info("maintenance pass: %s — %s (%.1fs, ok=%s)",
                                result.job, result.detail,
                                result.duration_s, result.ok)
            except Exception:  # noqa: BLE001 — a failed pass must never
                logger.exception("maintenance pass failed")  # noqa: BLE001
