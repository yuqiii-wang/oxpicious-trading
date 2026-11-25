"""server_cluster.maintainer.processes — the EMERGENCY resident-spawn
face of the maintainer (2026-11-21 executor demotion).

History: this server was the cluster's ONE resident-process EXECUTOR
(the 2026-11-13 detection/execution split) — the monitor and downloader
detectors POSTed every dead/stale/wedged restart here. The control
unification plan demoted it again (Phase 2): a supervisor spawns its
own residents in process — whoever CONFIGURES a child also SPAWNS it,
and a second ownership table (the old ``RESIDENT_NAMESPACE_PREFIXES``)
had to be kept in sync with ``ClusterConfig`` by hand for no benefit.

What remains here is the ONE direct-control path the plan grants the
maintainer: an EMERGENCY spawn of another area's resident, gated on
that area's scope server being UNREACHABLE. A request for service S:

* 403 — bad charset, or no area's ``module_prefixes`` own S (ownership
  truth stays in ``ClusterConfig``; the old namespace table is gone);
* 409 — S is this server's own module path (a process cannot reliably
  restart itself mid-request), or S's scope server ANSWERED the probe
  (GET /health): the body carries the scope server's url — route the
  request THERE;
* else (scope server down) the spawn fires: ``restart`` terminates the
  running instance first, ``start`` is the plain revival — and
  idempotent (a live instance is reported back, never duplicated: the
  emergency leg of the fleet ensure-up may fire while a resident runs
  with only its SERVER down — the children are detached).

Residents spawned here keep their own heartbeat/service-audit records;
when the scope server returns, its supervisor ADOPTS the running
instance (``find_running_module``) — desired-state semantics hold
(the 2026-11-21 plan, Phase 2 acceptance).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server_cluster._common.process_manager import (
    ProcessManager, find_running_module,
)

logger = logging.getLogger(__name__)

# This server's own module path: a self-restart request is refused (409) —
# the monitor holds this server's life in-process (bootstrap: the
# maintainer cannot answer a restart request that kills it mid-request).
SELF_SERVICE = "server_cluster.maintainer"

# The scope-server liveness probe before any emergency spawn (a short
# timeout: the probe IS the gate — a slow-but-alive scope server must
# answer 409 "route it there", not lose its residents to a duplicate
# spawn; the heartbeat max_parallel claims of every registering service
# remain the second net under a probe race).
_PROBE_TIMEOUT_S = 3.0

_SERVICE_CHARSET = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.")


class ProcessControlResponse(BaseModel):
    service: str
    action: str
    pid: int
    ok: bool = True


def owning_area(service: str) -> tuple[str, str, str]:
    """(area, service_name, base_url) of the area whose
    ``module_prefixes`` own ``service`` — the ONE ownership source
    (``ClusterConfig.areas``). ValueError when no area owns it."""
    from server_cluster._common.config import ClusterConfig

    cluster = ClusterConfig.load()
    best: tuple[int, str, str, str] | None = None
    for area, cfg in cluster.areas.items():
        for prefix in cfg.module_prefixes:
            if not service.startswith(prefix):
                continue
            score = len(prefix)
            if best is None or score > best[0]:
                best = (score, area, cfg.service,
                        f"http://{cfg.host}:{cfg.port}")
    if best is None:
        raise ValueError(
            f"{service!r} matches no area's module_prefixes in the "
            "cluster config — nothing owns it, so the maintainer will "
            "not touch it")
    return best[1], best[2], best[3]


def _validate_emergency(service: str) -> tuple[str, str]:
    """The emergency gate. 403 for a bad charset or an unowned service;
    409 for this server's own module path. Returns the owning area's
    (service name, base url) when the scope server is UNREACHABLE —
    the only state under which the spawn proceeds. A REACHABLE scope
    server raises 409 carrying its url (route the request there)."""
    if not service or any(c not in _SERVICE_CHARSET for c in service) \
            or ".." in service:
        raise HTTPException(
            status_code=403,
            detail=f"{service!r} is not a restartable service name")
    if service == SELF_SERVICE:
        raise HTTPException(
            status_code=409,
            detail="the maintainer cannot restart itself — the monitor "
            "holds this server's life in-process")
    # ONE ownership resolution (the gate's single config read — a
    # re-read could see a different config between probe and 409).
    area_key, area_service, base_url = owning_area(service)
    try:
        from _common.internal_request import request_json

        request_json("GET", f"{base_url}/health",
                     timeout_s=_PROBE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — unreachable IS the gate's pass
        return area_service, base_url
    raise HTTPException(
        status_code=409,
        detail=f"{service!r} belongs to the {area_key} area and its "
               f"scope server is up — route the request there "
               f"({base_url}); the maintainer spawns a resident only "
               "when that server cannot be reached")


def build_router(processes: ProcessManager) -> APIRouter:
    """The emergency process-control routes over ONE ProcessManager (the
    maintainer's own child table)."""
    router = APIRouter(tags=["processes"])

    @router.post("/processes/{service}/restart",
                 response_model=ProcessControlResponse)
    def restart_process(service: str) -> ProcessControlResponse:
        """EMERGENCY restart: terminate the running instance (if any) and
        spawn a fresh one — accepted ONLY while the service's scope
        server is unreachable (409 + its url otherwise). Sync def on
        purpose: the terminate grace window is blocking work (the jobs
        runner's rationale)."""
        _validate_emergency(service)
        pid = find_running_module(service)
        if pid is not None:
            processes.terminate(pid, service)
        new_pid, _log_path = processes.spawn(service, [])
        logger.info("EMERGENCY restart %s: %s → pid %d (scope server "
                    "unreachable)", service,
                    f"terminated {pid}" if pid is not None
                    else "nothing running", new_pid)
        return ProcessControlResponse(service=service, action="restart",
                                      pid=new_pid)

    @router.post("/processes/{service}/start",
                 response_model=ProcessControlResponse)
    def start_process(service: str) -> ProcessControlResponse:
        """EMERGENCY revival: spawn the service without terminating
        anything — accepted ONLY while its scope server is unreachable;
        idempotent (a live instance is reported back, not duplicated)."""
        _validate_emergency(service)
        live = find_running_module(service)
        if live is not None:
            logger.info("EMERGENCY start %s: already running as pid %d "
                        "— nothing spawned", service, live)
            return ProcessControlResponse(service=service,
                                          action="already_running",
                                          pid=live)
        new_pid, _log_path = processes.spawn(service, [])
        logger.info("EMERGENCY start %s as pid %d (scope server "
                    "unreachable)", service, new_pid)
        return ProcessControlResponse(service=service, action="start",
                                      pid=new_pid)

    return router
