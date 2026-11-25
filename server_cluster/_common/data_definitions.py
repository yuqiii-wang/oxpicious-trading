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


class HeartbeatDetail(BaseModel):
    """The heartbeat row's evidence for ONE service (the detail
    endpoint's b+d blocks): liveness (beat age), the stop record, the
    parallel-slot state, and — when the Phase 4 progress columns exist
    — the reported progress (phase machine + state + payload)."""

    service: str
    pid: int | None = None
    started_at: str | None = None
    beat_age_s: float | None = Field(default=None,
                                     description="Seconds since the last "
                                     "beat; None = no row yet.")
    scope: str | None = None
    sub_scope: str | None = None
    stop_reason: str | None = None
    is_normal_exit: bool | None = None
    stopped_at: str | None = None
    live_parallel: int | None = None
    max_parallel: int | None = None
    code_fingerprint: str | None = None
    fingerprint_stale: bool | None = Field(default=None,
                                           description="True when the row's "
                                           "code_fingerprint no longer "
                                           "matches a fresh disk hash; None "
                                           "= uncheckable (no row, unknown "
                                           "hash, or no fingerprint).")
    progress_phase: str | None = None
    progress_state: str | None = None
    progress_payload: dict[str, Any] | None = None
    progress_at: str | None = None
    progress_age_s: float | None = None


class DerivedProgress(BaseModel):
    """The missing-set oracle's answer for ONE service (the detail
    endpoint's c block) — derived from the DATA tables, never reported:
    ``kind`` picks the shape (``intraday`` = per-asset stream
    completeness counts; ``missing_set`` = expected-minus-missing over
    the identity/result pair)."""

    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ProcessDetail(BaseModel):
    """GET /processes/{service}/detail — the ONE unified query face
    (control unification plan Phase 3): the supervisor row when the
    service is a resident of this area, the heartbeat row's evidence,
    the derived (missing-set oracle) progress, the child-reported
    progress when present, and the service-log tail."""

    service: str
    area: str
    resident: ResidentStatus | None = Field(default=None,
                                            description="The supervisor "
                                            "row when the service is a "
                                            "resident of this area.")
    heartbeat: HeartbeatDetail | None = None
    derived_progress: DerivedProgress | None = None
    config: dict[str, Any] = Field(default_factory=dict,
                                   description="The service_config entry's "
                                   "control summary: resource_class, "
                                   "pausable, max_parallel, priority, "
                                   "is_resident.")
    log_tail: str = Field(default="", description="The service log's tail "
                          "(the same file the child writes).")
