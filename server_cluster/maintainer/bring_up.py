"""server_cluster.maintainer.bring_up — the daily 07:00 fleet bring-up.

MIGRATED from ``server_cluster.monitor.scheduler`` (2026-11): the
schedule (and its audit trail) belongs to the maintainer area; the
bring-back core stays ``monitor.fleet.FleetOps`` — the UI's
``POST /residents/ensure-up`` button shares that one implementation,
and the stale-watchdog spawn needs the monitor area's run registry.
The former dedicated ``DailyBringUp`` thread was folded into the
shared :class:`~server_cluster.maintainer.scheduler.MaintenanceScheduler`
at the same time: the job now just carries its own ``fire_at`` (07:00)
and writes its audit row itself, so one scheduler thread serves every
maintainer fire.

The job: ONE ``POST /residents/ensure-up`` against the monitor server
(start every desired-running-but-down resident; spawn a stale stream
watchdog), then the pass's ``monitor.service_audit`` row (exit_reason
``daily 07:00 bring-up``). A failed pass is that pass's ``ok=False``
result — never fatal to the server.

``MAINTAINER_BRING_UP_AT`` (env, ``HH:MM`` in CHINA time) overrides the
fire time for testing; the default stays 07:00.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from server_cluster._common.config import ClusterConfig
from server_cluster.maintainer.jobs import MaintenanceJob, PassAudit

CHINA_TZ = ZoneInfo("Asia/Shanghai")

# The fire time (China local) and the audit trail constants.
DEFAULT_FIRE_AT = "07:00"
FIRE_AT_ENV = "MAINTAINER_BRING_UP_AT"
AUDIT_SERVICE = "server_cluster.maintainer.bring_up"
AUDIT_EXIT_REASON = "daily 07:00 bring-up"

_REQUEST_TIMEOUT_S = 15.0


def monitor_base_url() -> str:
    """The monitor area's loopback base url from the loaded cluster
    config (the port honors cluster_config.json overrides) — shared
    with the nightly-pipeline job (the other monitor-driving fire)."""
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
    """One ensure-up pass over HTTP plus its audit row — the whole
    fleet check-and-restart (resident starts + the stale-watchdog
    spawn) happens in ``monitor.fleet.FleetOps``; this job only
    triggers and records it, so the UI button and the 07:00 fire stay
    one implementation. Its own ``fire_at`` group puts it on the shared
    scheduler's 07:00 pass."""

    name = "fleet_bring_up"
    description = ("Daily 07:00 fleet bring-up: start every "
                   "desired-running-but-down resident; spawn a stale "
                   "stream watchdog (via the monitor area's "
                   "/residents/ensure-up).")

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__()
        self._base_url = base_url
        self.fire_at = os.environ.get(FIRE_AT_ENV, DEFAULT_FIRE_AT)

    def _run(self) -> tuple[list[str], str]:
        started = datetime.now(timezone.utc)
        try:
            outcome = self._ensure_up()
        except Exception:
            PassAudit().record(AUDIT_SERVICE, started, False,
                               AUDIT_EXIT_REASON)
            raise
        ok = outcome.failed == 0
        detail = (f"brought up {outcome.brought_up}, "
                  f"{outcome.failed} failed, "
                  f"{len(outcome.actions)} action(s)")
        PassAudit().record(AUDIT_SERVICE, started, ok, AUDIT_EXIT_REASON)
        if not ok:
            raise RuntimeError(detail + " — " + "; ".join(
                a for a in outcome.actions if " ok=False" in a))
        return [], detail

    def _ensure_up(self) -> EnsureUpOutcome:
        url = f"{self._base_url or monitor_base_url()}" \
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
