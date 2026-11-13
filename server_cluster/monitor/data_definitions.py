"""server_cluster.monitor.data_definitions — the ops surface's typed
payloads (repo convention: pydantic models cross module boundaries)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class HealthCheckItem(BaseModel):
    """One fleet health observation — mirrors
    ``database.monitoring.CheckItem`` (kept local so this package does
    not import the monitoring module at route-definition time)."""

    label: str
    ok: bool
    message: str


class HealthCheckResponse(BaseModel):
    """GET /health-check — the replication + stream-liveness items and
    the same 0/1 exit the CLI maps them to."""

    items: list[HealthCheckItem]
    exit_code: int = Field(description="0 = all ok, 1 = at least one "
                           "alert (the database.monitoring CLI exit).")


class AreaProcess(BaseModel):
    """One resident row of one area server (the shape of the shared
    routers' ResidentStatus — kept loose here so an area's newer fields
    pass through)."""

    service: str
    desired: str | None = None
    state: str | None = None
    pid: int | None = None
    detail: dict = Field(default_factory=dict,
                         description="The source row's remaining fields.")


class AreaFleet(BaseModel):
    """One area server's slice of the fleet."""

    service: str
    area: str
    base_url: str
    reachable: bool
    error: str | None = None
    residents: list[AreaProcess] = Field(default_factory=list)


class AlwaysOnService(BaseModel):
    """One MONITORED_SERVICES always-on entry's heartbeat freshness (the
    cluster servers answer via their area rows; the standalone entries —
    the stream watchdog — only exist as heartbeat rows)."""

    service: str
    beat_age_s: int | None = None
    pid: int | None = None
    stale: bool = Field(description="No row or beat older than "
                        "service_heartbeat.STALE_AFTER_S.")


class FleetResponse(BaseModel):
    """GET /residents — the whole fleet in one view."""

    areas: list[AreaFleet]
    always_on: list[AlwaysOnService]


class EnsureUpAction(BaseModel):
    service: str
    area: str
    action: str = Field(description="started | spawned | already-up")
    ok: bool
    detail: str | None = None


class EnsureUpResponse(BaseModel):
    actions: list[EnsureUpAction]
    brought_up: int = Field(description="How many services were actually "
                            "started/spawned (already-up rows excluded).")


class OrchestrationStep(BaseModel):
    num: str
    script: str
    args: list[str] = Field(default_factory=list)
    can_be_parallel: bool = False
    note: str | None = None


class OrchestrationPlan(BaseModel):
    file: str
    steps: list[OrchestrationStep]


class OrchestrationRunRequest(BaseModel):
    file: str = Field(description="The orchestration file name under "
                      "scripts/orchestration/ (e.g. analysis_daily).")


class OrchestrationRunAccepted(BaseModel):
    tag: str
    file: str
    steps: list[OrchestrationStep]


class OrchestrationStepStatus(BaseModel):
    num: str
    script: str
    state: str = Field(description="pending | running | done | failed")
    tag: str | None = None
    exit_code: int | None = None


class OrchestrationRunStatus(BaseModel):
    tag: str
    file: str
    state: str = Field(description="running | done | failed")
    steps: list[OrchestrationStepStatus]
