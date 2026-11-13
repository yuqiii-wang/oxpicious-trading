"""server_cluster.maintainer.processes — the cluster's ONE resident-process
EXECUTOR, reached over HTTP by the detectors.

The supervision split (2026-11-13): the monitor (its 7 server-residents)
and the downloader (its 5 poller residents) own DETECTION — desired state,
adoption, crash backoff, stale-process fingerprints — but the restart
EXECUTION lives here: a detector that finds a resident dead or stale
POSTs ``/processes/{service}/restart`` and this server terminates the old
instance and spawns the fresh one (``ProcessManager`` — the same detached
Popen + day-log contract as every spawner in the repo).

Scope guards:
  * only the two resident NAMESPACES are restartable here
    (``server_cluster.*`` servers + ``downloads.*`` pollers) — anything
    else is 403;
  * the maintainer NEVER restarts itself (a process cannot reliably
    SIGTERM itself and then spawn its successor) — 409; the monitor
    restarts this server in-process, as always.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server_cluster._common.process_manager import (
    ProcessManager, find_running_module,
)

logger = logging.getLogger(__name__)

# The resident namespaces this server may restart — the two populations
# from server_cluster._common.config (SERVER_RESIDENTS + STREAM_POLLERS).
RESIDENT_NAMESPACE_PREFIXES = ("server_cluster.", "downloads.")

# This server's own module path: a self-restart request is refused (409) —
# the monitor holds this server's life in-process (bootstrap: the
# maintainer cannot answer a restart request that kills it mid-request).
SELF_SERVICE = "server_cluster.maintainer"


class ProcessControlResponse(BaseModel):
    service: str
    action: str
    pid: int
    ok: bool = True


def _validate_service(service: str) -> None:
    """403 outside the resident namespaces / bad module charset; 409 for
    this server's own module path."""
    if not service or any(c not in
                          "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                          "0123456789_." for c in service) \
            or ".." in service:
        raise HTTPException(
            status_code=403,
            detail=f"{service!r} is not a restartable service name")
    if not service.startswith(RESIDENT_NAMESPACE_PREFIXES):
        raise HTTPException(
            status_code=403,
            detail=f"{service!r} is outside this server's resident "
            f"namespaces {RESIDENT_NAMESPACE_PREFIXES} — only the cluster "
            "servers and the stream pollers restart here")
    if service == SELF_SERVICE:
        raise HTTPException(
            status_code=409,
            detail="the maintainer cannot restart itself — the monitor "
            "holds this server's life in-process")


def build_router(processes: ProcessManager) -> APIRouter:
    """The process-control routes over ONE ProcessManager (the
    maintainer's own child table)."""
    router = APIRouter(tags=["processes"])

    @router.post("/processes/{service}/restart",
                 response_model=ProcessControlResponse)
    def restart_process(service: str) -> ProcessControlResponse:
        """Terminate the running instance (if any) and spawn a fresh one —
        the endpoint the detectors (monitor/downloader supervisors) call
        for a dead or stale resident. Sync def on purpose: the terminate
        grace window is blocking work (the jobs runner's rationale)."""
        _validate_service(service)
        pid = find_running_module(service)
        if pid is not None:
            processes.terminate(pid, service)
        new_pid, _log_path = processes.spawn(service, [])
        logger.info("restart %s: %s → pid %d (executor: maintainer)",
                    service, f"terminated {pid}" if pid is not None
                    else "nothing running", new_pid)
        return ProcessControlResponse(service=service, action="restart",
                                      pid=new_pid)

    @router.post("/processes/{service}/start",
                 response_model=ProcessControlResponse)
    def start_process(service: str) -> ProcessControlResponse:
        """Spawn the service without terminating anything (a detector's
        plain revival path)."""
        _validate_service(service)
        new_pid, _log_path = processes.spawn(service, [])
        logger.info("start %s as pid %d (executor: maintainer)",
                    service, new_pid)
        return ProcessControlResponse(service=service, action="start",
                                      pid=new_pid)

    return router
