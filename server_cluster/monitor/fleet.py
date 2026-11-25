"""server_cluster.monitor.fleet — the fleet ops core + router.

``FleetOps`` aggregates the whole cluster in one view and brings
desired-running-but-down residents back up:

* ``snapshot()`` — every jobs area's ``GET /processes`` (8769–8772) plus
  the llm/config servers' ``GET /health``, merged with the always-on
  ``always_on_roster()`` heartbeat freshness (the completed 2026-11
  resident migration: every always-on service — the watchdog included,
  a monitor-area resident since then — has either an area supervisor
  row or a heartbeat row; the roster is the union view).
* ``ensure_up()`` — for every area resident whose state is ``down`` or
  ``backoff``: ``POST /processes/{service}/start`` (explicit operator
  intent — bypasses the crash-loop backoff, same as the UI button).
  This area's OWN residents (the seven servers, the six infra daemons
  and the stream watchdog itself — the mutual pair) flip through the
  in-process supervisor. An UNREACHABLE jobs area gains the EMERGENCY
  leg (2026-11-21 executor demotion Phase 2): its desired-running
  residents are asked of the maintainer's emergency spawn (the ONE
  direct-control path the maintainer keeps, gated server-side on the
  scope server being unreachable; adopted by the scope server on its
  return).

The daily 07:00 scheduler lived here (``scheduler.py``) and moved to
the maintainer area (``maintainer/bring_up.py``, 2026-11) — its fire
hits this area's ``POST /residents/ensure-up`` so the UI button and
the scheduled pass share ONE implementation.

HTTP fan-out rides :mod:`_common.internal_request` (the repo's ONE
internal HTTP executor — stdlib urllib under the hood, no new client
dependency for three loopback GETs) in ``asyncio.to_thread``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from .data_definitions import (
    AreaFleet, AreaProcess, AlwaysOnService, EnsureUpAction,
    EnsureUpResponse, FleetResponse, HealthCheckItem, HealthCheckResponse,
)
from _common.internal_request import InternalRequestError, request_json
from _common.service_heartbeat import STALE_AFTER_S, always_on_roster

logger = logging.getLogger(__name__)

# Job areas whose resident rows this server aggregates/controls (the
# llm/config areas serve their own apps — no /processes surface).
_JOBS_AREAS = ("downloader", "builder", "analyzer", "strategist")

_REQUEST_TIMEOUT_S = 5.0


def _fetch_json(base_url: str, path: str, method: str = "GET") -> tuple[bool, object | str]:
    """One loopback JSON call — (ok, decoded-body-or-error-text); the
    request rides _common.internal_request and this wrapper folds its
    ONE failure type into the fleet's (ok, error) row contract."""
    try:
        return True, request_json(method, f"{base_url}{path}",
                                  timeout_s=_REQUEST_TIMEOUT_S)
    except InternalRequestError as exc:
        return False, str(exc)


class FleetOps:
    """The fleet aggregation + bring-back logic (the routers and the
    daily scheduler share ONE instance — app.state.fleet)."""

    def __init__(self, config, processes, supervisor) -> None:
        self._config = config
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
            # The detail fold (2026-11-21 Phase 3): one call per area
            # returns each resident row WITH its unified detail payload
            # (heartbeat evidence + derived + reported progress + log
            # tail) — the Monitor UI's fleet views consume the merged
            # shape without new per-area endpoints.
            ok, body = _fetch_json(base_url, "/processes?detail=1")
            residents: list[AreaProcess] = []
            error: str | None = None
            if ok:
                for row in body:  # noqa: SON — list[dict] from JSON
                    known = {k: v for k, v in row.items()
                             if k in ("service", "desired", "state", "pid")}
                    detail = {k: v for k, v in row.items()
                              if k not in known}
                    # The area's fold parks the unified payload under
                    # ``detail`` — promote it to THIS detail bag so the
                    # fleet consumers read the payload fields (heartbeat
                    # evidence / derived progress / config) directly
                    # instead of through a double nest.
                    inner = detail.get("detail")
                    if isinstance(inner, dict) and (
                            "heartbeat" in inner
                            or "derived_progress" in inner):
                        detail = inner
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
        """The whole always-on roster's heartbeat freshness (the
        watchdog's scan charges + every area's residents — after the
        completed 2026-11 resident migration that union IS the fleet;
        the database-facing liveness view the CLI check prints)."""
        import psycopg

        from _common.db_commons import get_db_connection

        names = always_on_roster()
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
        jobs areas' via HTTP, this area's own via the supervisor — the
        seven servers, the six infra daemons AND the stream watchdog,
        the mutual pair). An UNREACHABLE jobs area adds the EMERGENCY
        leg (2026-11-21 executor demotion, Phase 2): its residents with
        config-desired ``running`` are asked of the MAINTAINER's
        emergency spawn (the maintainer probes the scope server itself,
        so a race degrades to its idempotent already-running answer) —
        the residents spawned that way are adopted by their scope
        server when it returns. NEVER raises — one area's failure is
        that area's error row."""
        actions: list[EnsureUpAction] = []

        for name in _JOBS_AREAS:
            service, base_url = self._area_base_urls()[name]
            ok, body = _fetch_json(base_url, "/processes")
            if not ok:
                actions.append(EnsureUpAction(
                    service=service, area=name, action="unreachable",
                    ok=False, detail=str(body)))
                actions.extend(self._emergency_start(name))
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

        # This area's OWN residents (the seven cluster servers, the six
        # infra daemons and the stream watchdog itself since the 2026-11
        # migration completed): flip any down/backoff one back to running
        # through the supervisor (explicit intent also clears its crash
        # backoff). No HTTP hop — the supervisor lives in-process.
        for row in self._supervisor.snapshot():
            if row.state not in ("down", "backoff"):
                continue
            self._supervisor.set_desired(row.service, "running")
            actions.append(EnsureUpAction(
                service=row.service, area=self._config.area,
                action="started", ok=True))

        return EnsureUpResponse(
            actions=actions,
            brought_up=sum(1 for a in actions
                           if a.action in ("started", "spawned",
                                           "emergency_start") and a.ok))

    def _emergency_start(self, area: str) -> list[EnsureUpAction]:
        """The ensure-up EMERGENCY leg for one unreachable jobs area:
        its config residents with desired ``running`` are asked of the
        maintainer's emergency spawn (``POST /processes/{svc}/start``
        there — the maintainer gates each request on the scope server
        being unreachable and answers idempotently for a live
        instance). One action row per resident; the leg itself never
        raises."""
        from server_cluster._common.config import ClusterConfig
        from server_cluster._common.maintainer_client import (
            emergency_start_via_maintainer,
        )

        actions: list[EnsureUpAction] = []
        for spec in ClusterConfig.load().areas[area].residents:
            if spec.desired != "running":
                continue
            pid = emergency_start_via_maintainer(spec.service)
            actions.append(EnsureUpAction(
                service=spec.service, area=area,
                action="emergency_start", ok=pid is not None,
                detail=None if pid is not None else
                "maintainer emergency spawn refused or unreachable"))
        return actions

    async def snapshot_async(self) -> FleetResponse:
        """The event-loop wrapper: the core is blocking (internal
        requests + psycopg) — run it in a worker thread (the llm-area
        to_thread discipline)."""
        import asyncio

        return await asyncio.to_thread(self.snapshot)

    async def ensure_up_async(self) -> EnsureUpResponse:
        import asyncio

        return await asyncio.to_thread(self.ensure_up)


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
