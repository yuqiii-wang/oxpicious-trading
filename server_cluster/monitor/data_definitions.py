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
    src_host: str | None = Field(default=None, description=(
        "Query-destination host group (downloads tiers' diff-src-"
        "parallel): consecutive steps with DISTINCT hosts share one "
        "concurrent wave; None runs the step alone, fully serial.")
    )
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


class OrchestrationInFlightRun(BaseModel):
    tag: str
    file: str


class OrchestrationInFlightResponse(BaseModel):
    runs: list[OrchestrationInFlightRun] = Field(
        default_factory=list, description="Every tier run currently in "
        "flight (the maintainer's nightly chain adopts these instead of "
        "racing a manual / UI-triggered run).")


class NonDailyTaskRow(BaseModel):
    """One non-daily task of the GET /non-daily inventory — the tier
    step (orchestration), its check config (non_daily_checks tree), and
    its current state row (monitor.non_daily_task_state) merged."""

    service: str
    tier: str
    step: str
    script: str
    args: list[str] = Field(default_factory=list)
    check: str | None = Field(default=None, description="The configured "
                             "check kind; None = schedule-only monitoring.")
    rerun_args: list[str] = Field(default_factory=list)
    rerun_eligible: bool = Field(description="Whether the weekend "
                                 "backfill may re-run this task.")
    state: str = Field(default="unknown", description="unknown | "
                       "complete | missing | check_failed (from the "
                       "label domain non_daily_state).")
    state_code: int = 0
    missing_count: int = 0
    missing_detail: dict | None = None
    attempts: int = 0
    last_check_at: str | None = None
    last_rerun_at: str | None = None
    next_check_at: str | None = None


class NonDailyInventoryResponse(BaseModel):
    """GET /non-daily — every non-daily tier's tasks with their check
    configs and current completeness state."""

    tasks: list[NonDailyTaskRow]


class NonDailyEvaluateResponse(BaseModel):
    """POST /non-daily/evaluate — one check pass over the configured
    tasks; the per-task verdicts land in monitor.non_daily_task_state."""

    evaluated: int
    complete: int
    missing: int
    check_failed: int


class NonDailyBackfillRequest(BaseModel):
    """POST /non-daily/backfill — the weekend re-run drive's body."""

    dry_run: bool = Field(default=False, description="Report the due "
                          "re-runs without submitting them.")


class NonDailyBackfillRerun(BaseModel):
    """One task the backfill drove (or, on dry_run, would drive)."""

    service: str
    tier: str
    module: str
    args: list[str] = Field(default_factory=list)
    dry_run: bool = False
    submitted: bool = False
    exit_code: int | None = None
    state_after: str | None = Field(default=None, description="The "
                                    "post-rerun re-check verdict.")


class NonDailyBackfillResponse(BaseModel):
    """POST /non-daily/backfill — the drive's summary."""

    evaluated: int
    missing: int
    due: list[str] = Field(default_factory=list)
    reruns: list[NonDailyBackfillRerun] = Field(default_factory=list)
    detail: str = ""
