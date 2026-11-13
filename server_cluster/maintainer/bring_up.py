"""server_cluster.maintainer.bring_up — the daily 07:00 fleet bring-up.

MIGRATED from ``server_cluster.monitor.scheduler`` (2026-11): the
schedule (and its audit trail) now belongs to the maintainer area; the
bring-back core stays ``monitor.fleet.FleetOps`` — the UI's
``POST /residents/ensure-up`` button shares that one implementation,
and the stale-watchdog spawn needs the monitor area's run registry.

The job: a daemon thread (the repo's ``Event.wait`` loop pattern)
sleeps until the next 07:00 Asia/Shanghai, fires ONE
``POST /residents/ensure-up`` against the monitor server (start every
desired-running-but-down resident; spawn a stale stream watchdog),
appends the pass to ``monitor.service_audit`` (exit_reason
``daily 07:00 bring-up``), and re-arms for the next day. A failed pass
is logged and retried at the next fire — never fatal to the server.

``MAINTAINER_BRING_UP_AT`` (env, ``HH:MM`` in CHINA time) overrides the
fire time for testing; the default stays 07:00.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from server_cluster._common.config import ClusterConfig
from server_cluster.maintainer.jobs import JobResult, MaintenanceJob

logger = logging.getLogger(__name__)

CHINA_TZ = ZoneInfo("Asia/Shanghai")

# The fire time (China local) and the audit trail constants.
DEFAULT_FIRE_AT = "07:00"
AUDIT_SERVICE = "server_cluster.maintainer.bring_up"
AUDIT_EXIT_REASON = "daily 07:00 bring-up"

_REQUEST_TIMEOUT_S = 15.0

_AUDIT_INSERT_SQL = """
INSERT INTO monitor.service_audit
    (service, scope, sub_scope, pid, started_at, ended_at,
     is_normal_exit, exit_reason)
VALUES (%s, 'server_cluster', 'maintainer', %s, %s, %s, %s, %s)
"""


def _fire_at_hour_minute() -> tuple[int, int]:
    raw = os.environ.get("MAINTAINER_BRING_UP_AT", DEFAULT_FIRE_AT)
    try:
        hh, mm = raw.split(":")
        return int(hh), int(mm)
    except ValueError:
        logger.warning("MAINTAINER_BRING_UP_AT=%r unreadable — using %s",
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


def _monitor_base_url() -> str:
    """The monitor area's loopback base url from the loaded cluster
    config (the port honors cluster_config.json overrides)."""
    cfg = ClusterConfig.load().areas["monitor"]
    return f"http://{cfg.host}:{cfg.port}"


class EnsureUpOutcome(BaseModel):
    """The decoded ``monitor.fleet.EnsureUpResponse`` subset this job
    reports on (typed at the boundary; the rest rides in ``raw``)."""

    brought_up: int = 0
    failed: int = 0
    actions: list[str] = Field(default_factory=list,
                               description="One 'service action ok=?' "
                               "line per ensure-up action.")


class FleetBringUpJob(MaintenanceJob):
    """One ensure-up pass over HTTP — the whole fleet check-and-restart
    (resident starts + the stale-watchdog spawn) happens in
    ``monitor.fleet.FleetOps``; this job only triggers and records it,
    so the UI button and the 07:00 fire stay one implementation."""

    name = "fleet_bring_up"
    description = ("Daily 07:00 fleet bring-up: start every "
                   "desired-running-but-down resident; spawn a stale "
                   "stream watchdog (via the monitor area's "
                   "/residents/ensure-up).")
    scheduled = False

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__()
        self._base_url = base_url

    def _run(self) -> tuple[list[str], str]:
        outcome = self._ensure_up()
        ok = outcome.failed == 0
        detail = (f"brought up {outcome.brought_up}, "
                  f"{outcome.failed} failed, "
                  f"{len(outcome.actions)} action(s)")
        if not ok:
            raise RuntimeError(detail + " — " + "; ".join(
                a for a in outcome.actions if " ok=False" in a))
        return [], detail

    def _ensure_up(self) -> EnsureUpOutcome:
        url = f"{self._base_url or _monitor_base_url()}" \
              "/residents/ensure-up"
        req = urllib.request.Request(
            url, method="POST",
            headers={"Content-Type": "application/json"}, data=b"")
        with urllib.request.urlopen(
                req, timeout=_REQUEST_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return EnsureUpOutcome(
            brought_up=int(body.get("brought_up", 0)),
            failed=sum(1 for a in body.get("actions", [])
                       if not a.get("ok")),
            actions=[f"{a.get('service')} {a.get('action')} "
                     f"ok={a.get('ok')}"
                     for a in body.get("actions", [])])


class DailyBringUp:
    """The once-a-day fleet bring-up loop (one thread per maintainer
    server; the pass runs through :class:`FleetBringUpJob` so its
    ``last_result`` shows on ``GET /jobs`` too)."""

    def __init__(self, job: FleetBringUpJob | None = None) -> None:
        self._job = job or FleetBringUpJob()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def job(self) -> FleetBringUpJob:
        return self._job

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="cluster-maintainer-daily-bring-up",
            daemon=True)
        self._thread.start()
        hh, mm = _fire_at_hour_minute()
        logger.info("daily bring-up armed: next fire at %02d:%02d "
                    "Asia/Shanghai (in %.0f s)", hh, mm,
                    _seconds_to_next_fire())

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.wait(_seconds_to_next_fire()):
            try:
                result = self._job.run()
                logger.info("daily bring-up pass: %s (%.1fs, ok=%s)",
                            result.detail, result.duration_s, result.ok)
                self._record_audit(result.ok)
            except Exception:  # noqa: BLE001 — a failed pass must never
                logger.exception("daily bring-up pass failed")  # noqa: BLE001
                self._record_audit(False)

    def _record_audit(self, ok: bool) -> None:
        """One audit row per pass — the trail the Service Monitor UI
        expands (same table the watchdog restarts write)."""
        now = datetime.now(timezone.utc)
        try:
            from _common.db_commons import get_db_connection

            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        _AUDIT_INSERT_SQL,
                        (AUDIT_SERVICE, os.getpid(), now, now,
                         ok, AUDIT_EXIT_REASON))
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 — audit loss is not an
            logger.warning("daily bring-up audit row failed: %s", exc)
