"""server_cluster.llm.routers.ops — the operational router:
liveness, stats, and the migrated dummy rate-limit controls.

* ``GET /health`` — liveness + per-model lifecycle states.
* ``GET /stats`` — uptime, per-model request counters, and the dummy
  controls' state (the HTTP-sim surface the harness asserts on).
* ``POST /control`` — the dummy knobs migrated verbatim from the
  former test.dummy_llm.server (fail_next / rate_limit_code / max_rpm /
  reset; unknown code → 400). They affect ONLY the dummy-1 simulation.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ..data_definitions.admin import (
    ControlRequest, ControlResponse, HealthResponse, StatsResponse,
)
from ..manager import ModelManager
from .deps import check_bearer
from .deps import find_dummy_backend
from .errors import openai_error

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    manager: ModelManager = request.app.state.manager
    return HealthResponse(
        service=request.app.state.service_name,
        uptime_s=manager.uptime_s,
        models={s.model_id: s.state for s in manager.status_all()})


@router.get("/stats", response_model=StatsResponse)
async def stats(request: Request) -> StatsResponse:
    manager: ModelManager = request.app.state.manager
    dummy = find_dummy_backend(manager)
    return StatsResponse(
        service=request.app.state.service_name,
        uptime_s=manager.uptime_s,
        per_model=manager.stats_snapshot(),
        dummy_controls=(dummy.controls() if dummy is not None
                        else _zeroed_controls()))


def _zeroed_controls() -> ControlResponse:
    """The zeroed controls answered when no dummy model is registered —
    the /stats shape stays complete."""
    return ControlResponse(fail_next=0, rate_limit_code="1302", max_rpm=0,
                           served=0, rejected_rate_limit=0, rejected_auth=0)


@router.post("/control", response_model=ControlResponse)
async def control(request: Request, req: ControlRequest) -> Any:
    manager: ModelManager = request.app.state.manager
    denied = check_bearer(request, request.app.state.config, manager)
    if denied is not None:
        return denied
    dummy = find_dummy_backend(manager)
    if dummy is None:
        return openai_error(
            404, "no dummy model is registered on this host — the "
            "rate-limit controls do not exist.")
    try:
        return dummy.apply_control(
            fail_next=req.fail_next, rate_limit_code=req.rate_limit_code,
            max_rpm=req.max_rpm, reset=req.reset)
    except ValueError as exc:
        return openai_error(400, str(exc))
