"""server_cluster._common.routers.processes — the area's resident-process
control: list + start/stop/restart. This is the takeover surface: the
supervisor's desired-state loop honors these flips ACROSS restarts (a
stopped resident stays stopped — the property the central watchdog
never had)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..data_definitions import ResidentControlResponse, \
    ResidentStatus
from ..deps import get_supervisor

router = APIRouter(tags=["processes"])


@router.get("/processes", response_model=list[ResidentStatus])
async def processes(request: Request) -> list[ResidentStatus]:
    """The area's resident rows (liveness-probed; empty for areas with
    no residents — every server serves the same surface)."""
    return get_supervisor(request).snapshot()


@router.post("/processes/{service}/start",
             response_model=ResidentControlResponse)
async def start_process(request: Request, service: str
                        ) -> ResidentControlResponse:
    return _control(request, service, "start")


@router.post("/processes/{service}/stop",
             response_model=ResidentControlResponse)
async def stop_process(request: Request, service: str
                       ) -> ResidentControlResponse:
    return _control(request, service, "stop")


@router.post("/processes/{service}/restart",
             response_model=ResidentControlResponse)
async def restart_process(request: Request, service: str
                          ) -> ResidentControlResponse:
    return _control(request, service, "restart")


def _control(request: Request, service: str,
             action: str) -> ResidentControlResponse:
    supervisor = get_supervisor(request)
    if action == "start":
        ok = supervisor.set_desired(service, "running")
    elif action == "stop":
        ok = supervisor.set_desired(service, "stopped")
    else:
        ok = supervisor.restart(service)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"service {service!r} is not a resident of this "
            "server's area")
    return ResidentControlResponse(service=service, action=action, ok=True)
