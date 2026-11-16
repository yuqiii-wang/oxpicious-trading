"""server_cluster.maintainer.non_daily — the Saturday non-daily
backfill fire.

The SCHEDULER side of the non-daily backfill (the CHECKS and the re-run
execution live in the monitor area — ``server_cluster/monitor/
non_daily.py``, the same split as the nightly chain): one fire group on
the shared :class:`~server_cluster.maintainer.scheduler.
MaintenanceScheduler`, default 05:00 Asia/Shanghai, that fires EVERY day
but only drives on SATURDAYS (weekends are non-trading days, so the
re-runs never race the trading-day nightly chain; a missed fire is not
caught up — the next Saturday covers it, and the job is runnable on
demand over the API with ``{"force": true}``).

The job POSTs the monitor area's ``/non-daily/backfill``: that drive
evaluates every configured non-daily task against the daily data
identities, re-runs each DUE missing task (module + tier args + the
config's rerun force args, through the run registry), and schedules the
weekly retry until the data is done loaded. This job reports the drive's
summary and appends one ``monitor.service_audit`` pass row.

``MAINTAINER_NONDAILY_AT`` (env, ``HH:MM`` China time) overrides the
fire time for testing.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from server_cluster.maintainer.bring_up import monitor_base_url
from server_cluster.maintainer.jobs import JobResult, MaintenanceJob, PassAudit

logger = logging.getLogger(__name__)

CHINA_TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_FIRE_AT = "05:00"
FIRE_AT_ENV = "MAINTAINER_NONDAILY_AT"
_REQUEST_TIMEOUT_S = 15.0

AUDIT_SERVICE = "server_cluster.maintainer.non_daily"
AUDIT_EXIT_REASON = "non-daily weekend backfill"

# Saturday — the weekend re-run day (weekday(): Monday == 0).
_BACKFILL_WEEKDAY = 5


class NonDailyBackfillRunRequest(BaseModel):
    """The job's on-demand run body (the scheduled fire uses the
    default: no force — Saturday gate on)."""

    force: bool = Field(default=False, description="Drive even when "
                        "today is not Saturday (the testing/manual "
                        "escape hatch).")


class NonDailyBackfillJob(MaintenanceJob):
    """The Saturday non-daily backfill — evaluate + re-run the missing
    non-daily tasks through the monitor area's /non-daily face."""

    name = "non_daily_backfill"
    description = ("Saturday 05:00 (China time) non-daily backfill: "
                   "drives the monitor area's /non-daily/backfill — "
                   "identity-grounded completeness checks over the "
                   "monthly/quarterly/semiannual tasks, re-running each "
                   "missing one with its force args (weekly retry until "
                   "the data is done loaded). Non-Saturday fires skip "
                   "(force bypasses); missed fires are covered by the "
                   "next Saturday.")

    def __init__(self) -> None:
        super().__init__()
        self._request = NonDailyBackfillRunRequest()
        self.fire_at = os.environ.get(FIRE_AT_ENV, DEFAULT_FIRE_AT)

    def run(self, request: NonDailyBackfillRunRequest | None = None
            ) -> JobResult:
        self._request = request or NonDailyBackfillRunRequest()
        try:
            return super().run()
        finally:
            self._request = NonDailyBackfillRunRequest()

    def _run(self) -> tuple[list[str], str]:
        started = datetime.now(timezone.utc)
        today = datetime.now(CHINA_TZ).date()
        if today.weekday() != _BACKFILL_WEEKDAY and not self._request.force:
            detail = (f"not Saturday ({today.isoformat()}) — backfill "
                      "skipped (force bypasses)")
            logger.info("non-daily backfill: %s", detail)
            PassAudit().record(AUDIT_SERVICE, started, True,
                               AUDIT_EXIT_REASON)
            return [], detail
        summary = self._drive_monitor()
        ok = "error" not in summary
        detail = (f"drive: {summary.get('evaluated', '?')} task(s) "
                  f"evaluated, {summary.get('missing', '?')} missing, "
                  f"{len(summary.get('due', []))} due — "
                  f"{summary.get('detail', '')}")
        if not ok:
            detail = f"drive FAILED: {summary.get('error', '')}"
        PassAudit().record(AUDIT_SERVICE, started, ok, AUDIT_EXIT_REASON)
        logger.info("non-daily backfill: %s", detail)
        if not ok:
            raise RuntimeError(detail)
        return [], detail

    def _drive_monitor(self) -> dict:
        """POST the monitor area's backfill drive (stdlib urllib — the
        fleet's no-new-dependency rule; the drive is minutes-long, so
        the timeout only bounds the CONNECT, not the drive)."""
        url = f"{monitor_base_url()}/non-daily/backfill"
        req = urllib.request.Request(
            url, method="POST",
            data=json.dumps({"dry_run": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(
                    req, timeout=3600.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.exception("non-daily backfill drive failed")
            return {"error": f"{type(exc).__name__}: {exc}"}
