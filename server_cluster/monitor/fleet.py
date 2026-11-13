"""server_cluster.monitor.fleet — the fleet ops core + router.

``FleetOps`` aggregates the whole cluster in one view and brings
desired-running-but-down residents back up:

* ``snapshot()`` — every jobs area's ``GET /processes`` (8769–8772) plus
  the llm/config servers' ``GET /health``, merged with the always-on
  ``MONITORED_SERVICES`` heartbeat freshness (the standalone entries —
  today the stream watchdog — exist only as heartbeat rows).
* ``ensure_up()`` — for every area resident whose state is ``down`` or
  ``backoff``: ``POST /processes/{service}/start`` (explicit operator
  intent — bypasses the crash-loop backoff, same as the UI button). A
  stale ``database.stream_watchdog`` heartbeat spawns the supervisor
  through the monitor area's own registry (it is nobody's resident —
  the WSL boot hook starts it, this is the recovery path).

The daily 07:00 scheduler lived here (``scheduler.py``) and moved to
the maintainer area (``maintainer/bring_up.py``, 2026-11) — its fire
hits this area's ``POST /residents/ensure-up`` so the UI button and
the scheduled pass share ONE implementation.

HTTP fan-out uses urllib in ``asyncio.to_thread`` (stdlib only — no new
client dependency for three loopback GETs).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from fastapi import APIRouter, Request

from .data_definitions import (
    AreaFleet, AreaProcess, AlwaysOnService, EnsureUpAction,
    EnsureUpResponse, FleetResponse, HealthCheckItem, HealthCheckResponse,
)
from _common.service_heartbeat import MONITORED_SERVICES, STALE_AFTER_S

logger = logging.getLogger(__name__)

# The standalone always-on service this area may spawn (the ensure-up
# watchdog path — nobody's resident, so the fleet core reaches it
# through the registry instead of an area's /processes).
WATCHDOG_SERVICE = "database.stream_watchdog"

# Registry tags for the spawns ensure-up may issue (the jobs surface's
# tag-dedupe identity — a second spawn while the first warms up is
# refused, never doubled).
SCHEDULER_TAGS = {
    WATCHDOG_SERVICE: "always-on:database.stream_watchdog",
}

# Job areas whose resident rows this server aggregates/controls (the
# llm/config areas serve their own apps — no /processes surface).
_JOBS_AREAS = ("downloader", "builder", "analyzer", "strategist")

_REQUEST_TIMEOUT_S = 5.0


def _fetch_json(base_url: str, path: str, method: str = "GET") -> tuple[bool, object | str]:
    """One loopback JSON call — (ok, decoded-body-or-error-text)."""
    url = f"{base_url}{path}"
    req = urllib.request.Request(
        url, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_S) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return False, str(exc)


class FleetOps:
    """The fleet aggregation + bring-back logic (the routers and the
    daily scheduler share ONE instance — app.state.fleet)."""

    def __init__(self, config, registry, processes, supervisor) -> None:
        self._config = config
        self._registry = registry
        self._processes = processes
        self._supervisor = supervisor

    # ------------------------------------------------------------------
    # area base urls
    # ------------------------------------------------------------------

    def _area_base_urls(self) -> dict[str, tuple[str, str]]:
        """area -> (service, base_url) from the loaded cluster config
        (ports honor cluster_config.json overrides)."""
        from server_cluster._common.config import ClusterConfig

        cluster = ClusterConfig.load()
        return {
            name: (cfg.service, f"http://{cfg.host}:{cfg.port}")
            for name, cfg in cluster.areas.items() if name in _JOBS_AREAS
        }

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> FleetResponse:
        from server_cluster._common.config import ClusterConfig

        areas: list[AreaFleet] = []
        for name in _JOBS_AREAS:
            service, base_url = self._area_base_urls()[name]
            ok, body = _fetch_json(base_url, "/processes")
            residents: list[AreaProcess] = []
            error: str | None = None
            if ok:
                for row in body:  # noqa: SON — list[dict] from JSON
                    known = {k: v for k, v in row.items()
                             if k in ("service", "desired", "state", "pid")}
                    detail = {k: v for k, v in row.items()
                              if k not in known}
                    residents.append(AreaProcess(**known, detail=detail))
            else:
                error = str(body)
            areas.append(AreaFleet(
                service=service, area=name, base_url=base_url,
                reachable=ok, error=error, residents=residents))

        for name in ("llm", "config", "maintainer"):
            cfg = ClusterConfig.load().areas[name]
            base_url = f"http://{cfg.host}:{cfg.port}"
            ok, body = _fetch_json(base_url, "/health")
            areas.append(AreaFleet(
                service=cfg.service, area=name, base_url=base_url,
                reachable=ok, error=None if ok else str(body)))

        return FleetResponse(areas=areas, always_on=self._always_on())

    def _always_on(self) -> list[AlwaysOnService]:
        """The MONITORED_SERVICES entries' heartbeat freshness (the
        database-facing liveness view the CLI check prints)."""
        import psycopg

        from _common.db_commons import get_db_connection

        names = list(MONITORED_SERVICES)
        beats: dict[str, tuple[int, int]] = {}
        try:
            conn = get_db_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT service, pid, EXTRACT(EPOCH FROM "
                        "(now() - beat_at))::bigint "
                        "FROM monitor.service_heartbeat "
                        "WHERE service = ANY(%s)", (names,))
                    beats = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            finally:
                conn.close()
        except psycopg.Error as exc:
            logger.warning("always-on heartbeat read failed: %s", exc)
            return [AlwaysOnService(service=s, stale=True) for s in names]
        rows = []
        for name in names:
            pid, age = beats.get(name, (None, None))
            rows.append(AlwaysOnService(
                service=name, pid=pid, beat_age_s=age,
                stale=age is None or age > int(STALE_AFTER_S)))
        return rows

    # ------------------------------------------------------------------
    # ensure-up
    # ------------------------------------------------------------------

    def ensure_up(self) -> EnsureUpResponse:
        """Bring the fleet up: start every down/backoff resident (the
        jobs areas' via HTTP, this area's own via the supervisor), spawn
        a stale stream watchdog. NEVER raises — one area's failure is
        that area's error row."""
        actions: list[EnsureUpAction] = []

        for name in _JOBS_AREAS:
            service, base_url = self._area_base_urls()[name]
            ok, body = _fetch_json(base_url, "/processes")
            if not ok:
                actions.append(EnsureUpAction(
                    service=service, area=name, action="unreachable",
                    ok=False, detail=str(body)))
                continue
            for row in body:
                state = row.get("state")
                if state not in ("down", "backoff"):
                    continue
                svc = row["service"]
                started, start_body = _fetch_json(
                    base_url, f"/processes/{svc}/start", method="POST")
                actions.append(EnsureUpAction(
                    service=svc, area=name, action="started",
                    ok=started, detail=None if started else str(start_body)))

        # This area's OWN residents (the seven cluster servers since the
        # 2026-11 migration): flip any down/backoff one back to running
        # through the supervisor (explicit intent also clears its crash
        # backoff). No HTTP hop — the supervisor lives in-process.
        for row in self._supervisor.snapshot():
            if row.state not in ("down", "backoff"):
                continue
            self._supervisor.set_desired(row.service, "running")
            actions.append(EnsureUpAction(
                service=row.service, area=self._config.area,
                action="started", ok=True))

        actions.extend(self._ensure_watchdog())
        return EnsureUpResponse(
            actions=actions,
            brought_up=sum(1 for a in actions
                           if a.action in ("started", "spawned") and a.ok))

    async def snapshot_async(self) -> FleetResponse:
        """The event-loop wrapper: the core is blocking (urllib +
        psycopg) — run it in a worker thread (the llm-area to_thread
        discipline)."""
        import asyncio

        return await asyncio.to_thread(self.snapshot)

    async def ensure_up_async(self) -> EnsureUpResponse:
        import asyncio

        return await asyncio.to_thread(self.ensure_up)

    def _ensure_watchdog(self) -> list[EnsureUpAction]:
        """The standalone always-on entries: a stale
        ``database.stream_watchdog`` beat means the supervisor itself is
        dead — spawn it through this area's registry (tag-deduped; a
        second spawn while the first warms up is refused). The OTHER
        always-on rows (db_health, the llm_agents daemons) are still the
        watchdog's charges — starting them here would fight it, so their
        staleness is reported, not acted on."""
        rows = self._always_on()
        actions: list[EnsureUpAction] = []
        for row in rows:
            if not row.stale:
                actions.append(EnsureUpAction(
                    service=row.service, area="monitor",
                    action="already-up", ok=True))
                continue
            if row.service != WATCHDOG_SERVICE:
                actions.append(EnsureUpAction(
                    service=row.service, area="monitor", action="stale",
                    ok=False,
                    detail="stale heartbeat — supervised by the stream "
                           "watchdog / WSL boot hook, not started here"))
                continue
            entry, already = self._registry.submit(
                WATCHDOG_SERVICE, [], SCHEDULER_TAGS[WATCHDOG_SERVICE])
            actions.append(EnsureUpAction(
                service=WATCHDOG_SERVICE, area="monitor",
                action="already-up" if already else "spawned",
                ok=True,
                detail=None if not already else "a spawn is already in "
                "flight under the registry tag"))
        return actions


# ---------------------------------------------------------------------------
# routers
# ---------------------------------------------------------------------------

router = APIRouter(tags=["fleet"])


@router.get("/health-check", response_model=HealthCheckResponse)
async def health_check() -> HealthCheckResponse:
    """The fleet health check (database.monitoring's probes) as JSON —
    the data_viz API's former ``python -m database.monitoring`` spawn."""
    items = await _probe_items()
    return HealthCheckResponse(
        items=items, exit_code=0 if all(i.ok for i in items) else 1)


async def _probe_items() -> list[HealthCheckItem]:
    def _run() -> list[HealthCheckItem]:
        # Lazy: database.monitoring pulls service_heartbeat → db_commons
        # (and transitively pandas) — never at route-definition time.
        from database.monitoring import HealthCheck

        probe = HealthCheck()
        found = probe.replication() + probe.streams()
        return [HealthCheckItem(label=i.label, ok=i.ok, message=i.message)
                for i in found]

    import asyncio

    return await asyncio.to_thread(_run)


@router.get("/residents", response_model=FleetResponse)
async def residents(request: Request) -> FleetResponse:
    return await _ops(request).snapshot_async()


@router.post("/residents/ensure-up", response_model=EnsureUpResponse)
async def ensure_up(request: Request) -> EnsureUpResponse:
    return await _ops(request).ensure_up_async()


def _ops(request):
    return request.app.state.fleet
