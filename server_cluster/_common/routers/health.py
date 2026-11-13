"""server_cluster._common.routers.health — liveness + operational
snapshot (the ops-router shape of the llm-area host template).

* ``GET /health`` — the liveness probe (HTTP + the heartbeat thread are
  the two liveness signals; the watchdog reads the latter).
* ``GET /stats`` — uptime, registry counts, and the residents' rows."""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Request

from ..data_definitions import HealthResponse, StatsResponse

# One server start per process — stamped at import (uvicorn reloads
# don't re-import; the lifespan-based clocks aren't worth the state).
_STARTED_AT = time.time()

router = APIRouter(tags=["ops"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    config = request.app.state.config
    return HealthResponse(
        service=config.service, area=config.area, pid=os.getpid(),
        uptime_s=time.time() - _STARTED_AT)


@router.get("/stats", response_model=StatsResponse)
async def stats(request: Request) -> StatsResponse:
    config = request.app.state.config
    return StatsResponse(
        service=config.service, area=config.area, pid=os.getpid(),
        uptime_s=time.time() - _STARTED_AT,
        runs=request.app.state.registry.counts(),
        residents=request.app.state.supervisor.snapshot())
