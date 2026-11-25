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
    priority: int = Field(default=0, description=(
        "The step's give-way rank from its service's service_config "
        "entry (downloads domain +100 over the freq rank; higher = "
        "wins conflicts — a strictly lower-priority step holds back / "
        "stops while this one runs)."))


class OrchestrationPlan(BaseModel):
    file: str
    steps: list[OrchestrationStep]


class OrchestrationRunRequest(BaseModel):
    file: str = Field(description="The orchestration file name under "
                      "scripts/orchestration/ (e.g. analysis_daily).")
    arrange: bool = Field(default=False, description=(
        "Arrange the tier first (the maintainer's nightly chain passes "
        "true): upsert every step service's verdict into "
        "monitor.orchestration_task_state — its data pre-check where one "
        "is declared (the latest closed sessions absent from its data "
        "table, and whether their source csv is staged), else its "
        "freq-window verdict — and SKIP the not-due steps (state "
        "skipped_not_due) instead of spawning them to internally no-op "
        "off-cadence. The arrangement itself is recorded even with "
        "force — only the skipping is bypassed.")
    )
    force: bool = Field(default=False, description=(
        "Run every step regardless of the arrangement (a manual re-run's "
        "escape hatch — the steps' own incremental guards still apply).")
    )


class OrchestrationRunAccepted(BaseModel):
    tag: str
    file: str
    steps: list[OrchestrationStep]


class OrchestrationStepStatus(BaseModel):
    num: str
    script: str
    state: str = Field(description="pending | waiting_priority | running "
                        "| done | failed | skipped_not_due "
                        "(waiting_priority = the step held back while a "
                        "STRICTLY higher-priority run is in flight — the "
                        "service_config priority give-way; it resumes "
                        "when the blocker clears. skipped_not_due = the "
                        "arrangement's verdict: the service's data is "
                        "present / its source csv is not staged yet (its "
                        "data pre-check), or its freq window holds — the "
                        "arranged run never spawned it.)")
    tag: str | None = None
    exit_code: int | None = None
    priority: int = Field(default=0, description="The step's give-way "
                          "rank (see OrchestrationStep.priority).")


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


class ArrangementTaskRow(BaseModel):
    """One orchestration-tier service's arrangement row of GET
    /arrangement — the current monitor.orchestration_task_state row,
    state decoded through the label index."""

    service: str
    tier: str
    step: str
    freq: str = Field(description="The step's cadence label (daily | "
                      "weekly | monthly | quarterly | semiannually; "
                      "'unknown' when unconfigured). Residents "
                      "(is_resident — always live) are not arranged and "
                      "hold no row.")
    priority: int = 0
    state: str = Field(description="unknown | due | not_due (from the "
                       "label domain orchestration_plan_state).")
    state_code: int = 0
    basis: str = Field(default="calendar", description="WHAT the verdict "
                       "rests on (from the label domain "
                       "orchestration_plan_basis): calendar (the freq "
                       "rule — no data pre-check declared) | data_present "
                       "| db_missing | csv_present | csv_missing | "
                       "check_failed (the data pre-check's reading).")
    basis_code: int = 0
    missing_count: int = Field(default=0, description="Sessions the data "
                               "pre-check's verdict turns on — to fetch, "
                               "to load, or absent from the data table "
                               "(0 = none, or the calendar rule decided).")
    missing_detail: dict | None = Field(default=None, description=(
        "The pre-check's session lists — the probed window, the absent "
        "sessions and which of them have their source csv staged (or "
        "the error for check_failed); None under the calendar rule."))
    last_success_at: str | None = None
    due_at: str | None = Field(default=None, description=(
        "The Shanghai-calendar boundary the arrangement keys on (NULL = "
        "no boundary: never succeeded / a resident (always lives, no "
        "freq) / occasionally / on_demand / once).")
    )
    arranged_at: str | None = None


class ArrangementInventoryResponse(BaseModel):
    """GET /arrangement — every arranged service's current verdict."""

    tasks: list[ArrangementTaskRow]


class ArrangementRefreshResponse(BaseModel):
    """POST /arrangement/refresh (and the arrange side of an arranged
    /orchestration/run) — one arrangement pass's summary."""

    tiers: int = Field(description="Tier files arranged.")
    services: int = Field(description="Distinct services arranged.")
    due: int = 0
    not_due: int = 0
    not_due_services: list[str] = Field(
        default_factory=list, description="The not-due services (the "
        "arranged run's skip set).")
    by_basis: dict[str, int] = Field(default_factory=dict,
                                     description="Services per basis label "
                                     "(calendar included) — how many "
                                     "verdicts the data pre-check "
                                     "decided, and what it found.")
    check_failed_services: list[str] = Field(
        default_factory=list, description="Services whose pre-check "
        "probe errored — arranged due fail-open (a caution row each).")


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
    yields: int = Field(default=0, description="Times the re-run was "
                        "stopped mid-flight to give way to a strictly "
                        "higher-priority orchestration run (each stop "
                        "waits the blocker out, then resumes the task "
                        "as a fresh child).")
    state_after: str | None = Field(default=None, description="The "
                                    "post-rerun re-check verdict.")


class NonDailyBackfillResponse(BaseModel):
    """POST /non-daily/backfill — the drive's summary."""

    evaluated: int
    missing: int
    due: list[str] = Field(default_factory=list)
    reruns: list[NonDailyBackfillRerun] = Field(default_factory=list)
    detail: str = ""
