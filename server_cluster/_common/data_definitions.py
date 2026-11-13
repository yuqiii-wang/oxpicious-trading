"""server_cluster._common.data_definitions — the cluster API's typed
payloads (repo convention: payloads crossing module boundaries are
pydantic BaseModels; validate with ``model_validate``, emit with
``model_dump()``). One module per app (the llm-area host shape) so
routers and the Express client share one contract surface.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .resident_supervisor import ResidentStatus


class RunRequest(BaseModel):
    """POST /jobs/run — one area cmd to run detached."""

    module: str = Field(description="Dotted module path under this "
                        "server's area (e.g. downloads.bond.shibor).")
    args: list[str] = Field(default_factory=list,
                            description="CLI args after ``python -m``.")
    tag: str | None = Field(default=None, description="The process-id-tag "
                            "identity (dedupe + status key). Omitted → "
                            "derived deterministically from module+args.")


class RunAccepted(BaseModel):
    """POST /jobs/run response — the Express ``already_running`` contract
    included verbatim."""

    tag: str
    module: str
    args: list[str] = Field(default_factory=list)
    pid: int | None = None
    already_running: bool = False
    log_path: str = ""


class RunStatusResponse(BaseModel):
    """GET /jobs/status — one enriched entry per requested tag (unknown
    tags answer ``running: false``)."""

    statuses: dict[str, dict[str, Any]] = Field(default_factory=dict)


class StopResponse(BaseModel):
    """POST /jobs/{tag}/stop — True when a run was addressed."""

    tag: str
    stopped: bool


class ResidentControlResponse(BaseModel):
    """POST /processes/{service}/(start|stop|restart) — the control
    verdict (``ok`` is False for an unknown service or a refused
    start)."""

    service: str
    action: str
    ok: bool


class HealthResponse(BaseModel):
    """GET /health — the liveness probe (the house shape: service name +
    uptime; the heartbeat beats are the watchdog's own signal)."""

    service: str
    area: str
    status: str = "ok"
    pid: int
    uptime_s: float


class StatsResponse(BaseModel):
    """GET /stats — the operational snapshot the monitor surfaces."""

    service: str
    area: str
    pid: int
    uptime_s: float
    runs: dict[str, int] = Field(default_factory=dict,
                                 description="Registry counts: running / "
                                 "finished_stored / adopted.")
    residents: list[ResidentStatus] = Field(default_factory=list)
