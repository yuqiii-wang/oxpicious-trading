"""server_cluster.maintainer.scheduler — the daily maintenance fires.

The repo's ``Event.wait`` daemon-thread pattern: sleeps until the next
fire time (Asia/Shanghai), runs the due
:class:`~server_cluster.maintainer.jobs.MaintenanceJob` group once, and
re-arms for the next day. Jobs form FIRE GROUPS by their ``fire_at``:
``None`` joins the shared maintenance pass (``MAINTAINER_FIRE_AT``,
default 03:30); a set ``HH:MM`` fires at its own time (the 07:00 fleet
bring-up, the 16:00 nightly pipeline chain). One thread serves every
group — it always sleeps to the EARLIEST next fire. A failed job is
that job's ``ok=False`` result — never fatal to the server.

``MAINTAINER_FIRE_AT`` (env, ``HH:MM`` China time) overrides the shared
pass's fire time for testing; the default stays 03:30 (before the 07:00
bring-up and the day's pipelines). A missed fire (server down at the
time) is not caught up — the next day's pass covers it, and every job
is runnable on demand over the API.
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
_FIRE_AT_ENV = "MAINTAINER_FIRE_AT"

# When no group is armed (every job scheduled=False) the loop still
# wakes hourly rather than parking forever.
_IDLE_RECHECK_S = 3600.0


def _parse_fire_at(raw: str, source: str, default: str) -> tuple[int, int]:
    try:
        hh, mm = raw.split(":")
        return int(hh), int(mm)
    except ValueError:
        logger.warning("%s=%r unreadable — using %s", source, raw, default)
        hh, mm = default.split(":")
        return int(hh), int(mm)


def _fire_at_hour_minute() -> tuple[int, int]:
    """The SHARED pass's fire time (jobs with their own ``fire_at``
    resolve theirs in their own module — one env per fire)."""
    return _parse_fire_at(
        os.environ.get(_FIRE_AT_ENV, DEFAULT_FIRE_AT),
        _FIRE_AT_ENV, DEFAULT_FIRE_AT)


def effective_fire_at(job) -> str:
    """The job's normalized ``HH:MM`` fire time — its own ``fire_at`` or
    the shared default."""
    hh, mm = _fire_at_hour_minute()
    return job.fire_at or f"{hh:02d}:{mm:02d}"


def _seconds_to_next_fire(fire_at: str,
                          now: datetime | None = None) -> float:
    """Seconds until the next ``fire_at`` China-time fire (today's if it
    has not passed yet, otherwise tomorrow's)."""
    hh, mm = _parse_fire_at(fire_at, "fire_at", DEFAULT_FIRE_AT)
    now = now or datetime.now(CHINA_TZ)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


class MaintenanceScheduler:
    """The once-a-day passes over the registered jobs — one fire group
    at a time (one thread per maintainer server)."""

    def __init__(self, jobs: list) -> None:
        self._jobs = jobs
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def fire_groups(self) -> dict[str, list]:
        """The scheduled jobs grouped by effective fire time (each
        group's list keeps registration order)."""
        groups: dict[str, list] = {}
        for job in self._jobs:
            if job.scheduled:
                groups.setdefault(effective_fire_at(job), []).append(job)
        return groups

    def default_group(self) -> list:
        """The shared-pass jobs (``fire_at`` unset) — the run-all set."""
        return [job for job in self._jobs
                if job.scheduled and job.fire_at is None]

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="cluster-maintainer-daily",
            daemon=True)
        self._thread.start()
        groups = self.fire_groups()
        armed = "; ".join(
            f"{fire_at} [{', '.join(j.name for j in jobs)}]"
            for fire_at, jobs in sorted(groups.items()))
        logger.info("maintenance armed: %s (next in %.0f s)",
                    armed, self._next_fire()[0])

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def run_all(self) -> list:
        """One pass over the SHARED-pass jobs now (the daily fire and
        the API's run-all share this; jobs in their own fire groups —
        the 07:00 bring-up, the 16:00 pipeline — fire on their own
        schedule or per-name on demand)."""
        return [job.run() for job in self.default_group()]

    def _next_fire(self) -> tuple[float, list]:
        """(seconds until the earliest group's next fire, that group)."""
        now = datetime.now(CHINA_TZ)
        best_s: float | None = None
        best_jobs: list = []
        for fire_at, jobs in self.fire_groups().items():
            seconds = _seconds_to_next_fire(fire_at, now)
            if best_s is None or seconds < best_s:
                best_s, best_jobs = seconds, jobs
        if best_s is None:
            return _IDLE_RECHECK_S, []
        return best_s, best_jobs

    def _loop(self) -> None:
        while True:
            wait_s, due = self._next_fire()
            if self._stop.wait(max(wait_s, 0.0)):
                break
            for job in due:
                try:
                    result = job.run()
                    logger.info("maintenance pass (%s): %s — %s "
                                "(%.1fs, ok=%s)",
                                effective_fire_at(job), result.job,
                                result.detail, result.duration_s,
                                result.ok)
                except Exception:  # noqa: BLE001 — a failed pass must
                    logger.exception(  # never take the server down
                        "maintenance job %s raised", job.name)
