"""server_cluster.monitor.arrangement — the orchestration arrangement.

The freq-to-run already lives in the service_config static tree
(``database/sql/monitoring/static/service_config/<dotted.service>.json``
— ``freq``); this module derives the RUN / DON'T-RUN decision from it
and persists it as the durable arrangement
(``monitor.orchestration_task_state``) that the nightly chain consumes
and the Monitor UI's Orchestration page shows. The MAINTAINER arranges:
its nightly chain (``server_cluster/maintainer/pipeline.py``) posts each
tier with ``arrange: true``, so the orchestration runner SKIPS not-due
steps (state ``skipped_not_due``) instead of spawning them to internally
no-op off-cadence; a manual run's ``force`` bypasses the skipping.

The arrangement rule is Shanghai-CALENDAR anchored, not a rolling
window: the chain fires at a fixed clock time (16:00 China), so a
rolling 24h window would arrange a daily service not_due while
yesterday's run is minutes short of 24h and it would alternate days.
Instead each freq maps to the next CALENDAR boundary after the last
successful exit (``monitor.service_audit`` normal exits):

- daily — due again the next Shanghai day (a same-day success, e.g. a
  morning manual run, holds it for tonight's chain);
- weekly — due again 7 calendar days later;
- monthly / quarterly / semiannually — due again at the next month /
  quarter / half-year start (the in-module cadence guards' semantics:
  a quarterly composition refreshes at quarter start);
- once — satisfied by any success ever, never due again;
- occasionally / on_demand / unknown freq — no boundary, due
  every pass (the spawn decision is left to the step itself).

RESIDENTS (``is_resident`` services — always live, no freq) are NOT
arranged at all: a resident has no "when to run" verdict (it always
lives — the resident supervisor / ensure-up path owns its liveness), so
the arranger skips it and it holds no row in
``monitor.orchestration_task_state``. A resident that ALSO rides a tier
step (the stream poller's ``--options-eod`` pass in downloads_daily)
still SPAWNS every arranged pass — excluded from the arrangement means
never in the not-due skip set, not "never run"; the spawned child
dedupes onto the live resident through the heartbeat's max_parallel
claim (EXIT_ALREADY_RUNNING, counted OK).

Safeguard: a tier step WITHOUT its own service_config file (the
default.json fallback) is always arranged due — an unconfigured step
keeps the run-every-pass behavior instead of being silently skipped off
the default.json ``on_demand`` freq.

The board the UI shows (running / to-run queue / recently no need) is
classified at the data_viz API from this table joined against the live
heartbeat and the audit trail — GET /arrangement here is the raw rows.

Endpoints (sync ``def`` on purpose — one arrange pass is a quick grouped
read + bounded upsert batch on the threadpool, like the non-daily face):
 - ``GET  /arrangement``         — the current arrangement rows.
 - ``POST /arrangement/refresh`` — one pass over every tier file.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

from _common.db_commons import get_db_connection_async
from _common.labels import ORCHESTRATION_PLAN_STATE_CODE
from _common.service_heartbeat import (
    freq_for, has_own_service_config, is_resident_for, priority_for,
)
from _common.sql_views import render_sql_view
from _common.sql_writes import load_sql_write
from .data_definitions import (
    ArrangementInventoryResponse, ArrangementRefreshResponse,
    ArrangementTaskRow,
)
from .orchestration import ORCHESTRATION_DIR, OrchestrationRunner

logger = logging.getLogger(__name__)

CHINA_TZ = ZoneInfo("Asia/Shanghai")

_STATE_VIEW = "server_cluster/monitor/orchestration/task_state"
_LAST_SUCCESS_VIEW = "server_cluster/monitor/orchestration/last_success_by_service"
_UPSERT_WRITE = ("server_cluster/monitor/orchestration/"
                 "orchestration_task_state/upsert")

# Day-step calendar freqs (due again N Shanghai days after the last
# success). Month-boundary freqs (monthly / quarterly / semiannually)
# roll to the next calendar period start; ``once`` is satisfied by any
# success; every other freq has no boundary.
_CALENDAR_STEP_DAYS: dict[str, int] = {"daily": 1, "weekly": 7}

# The freqs whose verdict a calendar boundary decides.
_BOUNDARY_FREQS = frozenset(_CALENDAR_STEP_DAYS) | {
    "monthly", "quarterly", "semiannually", "once"}


def _calendar_due_at(freq: str, last_success: datetime) -> datetime:
    """The next Shanghai-calendar boundary after ``last_success`` for a
    boundary freq — midnight Asia/Shanghai of the next day / week / month
    / quarter / half-year. Pure calendar arithmetic on the LOCAL date
    (the trading calendar's timezone)."""
    d = last_success.astimezone(CHINA_TZ).date()
    if freq in _CALENDAR_STEP_DAYS:
        nxt = d + timedelta(days=_CALENDAR_STEP_DAYS[freq])
    elif freq == "monthly":
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    elif freq == "quarterly":
        quarter_month = (d.month - 1) // 3 * 3 + 4  # next quarter's 1st
        if quarter_month > 12:
            nxt = date(d.year + 1, 1, 1)
        else:
            nxt = date(d.year, quarter_month, 1)
    elif freq == "semiannually":
        nxt = date(d.year + (d.month >= 7), 7 if d.month < 7 else 1, 1)
    else:  # pragma: no cover — the verdict never routes other freqs here
        raise ValueError(f"freq {freq!r} has no calendar boundary")
    return datetime.combine(nxt, time.min, tzinfo=CHINA_TZ)


class OrchestrationArranger:
    """The arrangement writer + inventory face (one instance per server,
    mounted on ``app.state.arrangement``; thread-safe — the router
    resolves the instance off app.state)."""

    def __init__(self) -> None:
        self._arrange_lock = threading.Lock()

    # ------------------------------------------------------------------
    # inventory (GET /arrangement)
    # ------------------------------------------------------------------

    def inventory(self) -> ArrangementInventoryResponse:
        """The current arrangement rows, state decoded through the label
        index (the shared view — the data_viz board reads the joined
        form; this is the monitor area's own raw face)."""
        view = render_sql_view(_STATE_VIEW)
        rows = asyncio.run(self._fetch(view.sql, list(view.params)))
        tasks = [
            ArrangementTaskRow(
                service=str(r["service"]), tier=str(r["tier"]),
                step=str(r["step"]), freq=str(r["freq"]),
                priority=int(r["priority"]),
                state=str(r["state_label"] or "unknown"),
                state_code=int(r["state"]),
                last_success_at=_iso_or_none(r["last_success_at"]),
                due_at=_iso_or_none(r["due_at"]),
                arranged_at=_iso_or_none(r["arranged_at"]))
            for r in rows
        ]
        return ArrangementInventoryResponse(tasks=tasks)

    # ------------------------------------------------------------------
    # arrange (POST /arrangement/refresh; the run path's arrange side)
    # ------------------------------------------------------------------

    def arrange(self, tiers: list[str] | None = None
                ) -> ArrangementRefreshResponse:
        """One arrangement pass: resolve the tier universe, read every
        service's last successful exit in ONE grouped query, compute the
        calendar-boundary verdicts, and upsert the rows. ONE pass at a
        time (a refresh racing an arranged tier run would flap the
        verdicts under the runner's feet)."""
        if not self._arrange_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail="an arrangement pass is already running")
        try:
            tier_files = self._tier_files(tiers)
            universe = self._universe(tier_files)
            successes = asyncio.run(self._fetch_last_success())
            now_cn = datetime.now(CHINA_TZ)
            plan_code = ORCHESTRATION_PLAN_STATE_CODE
            rows: list[tuple] = []
            due = not_due = 0
            not_due_services: list[str] = []
            for service, tier, step in universe:
                if is_resident_for(service):
                    # An always-live service has no "when to run" verdict
                    # (see the module docstring) — no row, never skipped.
                    continue
                freq = freq_for(service)
                state, due_at = self._verdict(
                    freq, has_own_service_config(service),
                    successes.get(service), now_cn)
                rows.append((
                    service, tier, step, freq or "unknown",
                    priority_for(service), state,
                    successes.get(service), due_at))
                if state == plan_code["due"]:
                    due += 1
                else:
                    not_due += 1
                    not_due_services.append(service)
            if rows:
                asyncio.run(self._write_rows(rows))
            logger.info(
                "arrangement: %d tier(s), %d service(s): %d due, %d "
                "not_due", len(tier_files), len(rows), due, not_due)
            return ArrangementRefreshResponse(
                tiers=len(tier_files), services=len(rows), due=due,
                not_due=not_due, not_due_services=not_due_services)
        finally:
            self._arrange_lock.release()

    # ------------------------------------------------------------------
    # verdict
    # ------------------------------------------------------------------

    @staticmethod
    def _verdict(freq: str | None, own_config: bool,
                 last_success: datetime | None,
                 now_cn: datetime) -> tuple[int, datetime | None]:
        """(state code, due_at) for one service — the calendar-boundary
        rule of the module docstring. due_at is the boundary the verdict
        keys on (None = no boundary: never succeeded / once / a no-window
        freq / unconfigured)."""
        plan_code = ORCHESTRATION_PLAN_STATE_CODE
        if not own_config or freq not in _BOUNDARY_FREQS:
            # Unconfigured or no cadence window (a resident — always
            # lives, no freq — / occasionally / on_demand / unknown): due
            # every pass, the step decides.
            return plan_code["due"], None
        if freq == "once":
            return (plan_code["not_due"] if last_success is not None
                    else plan_code["due"]), None
        if last_success is None:
            return plan_code["due"], None
        due_at = _calendar_due_at(freq, last_success)
        return (plan_code["due"] if now_cn >= due_at
                else plan_code["not_due"]), due_at

    # ------------------------------------------------------------------
    # universe + persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _tier_files(tiers: list[str] | None) -> list[str]:
        """The tier files to arrange: the requested subset (validated
        through the plan reader) or every file under
        scripts/orchestration/."""
        if tiers is None:
            return sorted(p.stem for p in ORCHESTRATION_DIR.glob("*.json"))
        files: list[str] = []
        for tier in tiers:
            OrchestrationRunner.plan(tier)  # 404/422 for a bad name
            files.append(tier)
        return files

    @staticmethod
    def _universe(tier_files: list[str]
                  ) -> list[tuple[str, str, str]]:
        """(service, tier, step) for every tier file's steps — first
        occurrence per service (a service listed in two tiers arranges
        under the first, same contract as the non-daily tier map)."""
        seen: set[str] = set()
        universe: list[tuple[str, str, str]] = []
        for tier in tier_files:
            try:
                plan = OrchestrationRunner.plan(tier)
            except HTTPException:
                logger.warning("arrangement: tier %s unreadable", tier)
                continue
            for step in plan.steps:
                if step.script in seen:
                    continue
                seen.add(step.script)
                universe.append((step.script, tier, step.num))
        return universe

    @staticmethod
    async def _fetch(sql: str, params: list) -> list:
        conn = await get_db_connection_async()
        try:
            return await conn.fetch(sql, *params)
        finally:
            await conn.close()

    @staticmethod
    async def _fetch_last_success() -> dict[str, datetime]:
        """Every service's last successful exit (one grouped pass over
        the audit trail — never a per-service query loop)."""
        view = render_sql_view(_LAST_SUCCESS_VIEW)
        rows = await OrchestrationArranger._fetch(
            view.sql, list(view.params))
        return {str(r["service"]): r["last_success_at"]
                for r in rows if r["last_success_at"] is not None}

    @staticmethod
    async def _write_rows(rows: list[tuple]) -> None:
        """Upsert the arrangement rows (executemany over the action's
        SQL text — the universe is bounded by the tier files, one
        transaction)."""
        sql = load_sql_write(_UPSERT_WRITE)
        conn = await get_db_connection_async()
        try:
            async with conn.transaction():
                await conn.executemany(sql, rows)
        finally:
            await conn.close()


def _iso_or_none(value: object) -> str | None:
    """A DB timestamp → ISO string for the response payload (None-safe)."""
    if isinstance(value, datetime):
        return value.isoformat()
    return None


router = APIRouter(tags=["arrangement"])


# Sync ``def`` on purpose — the ops classes' asyncio.run faces must run
# on the threadpool (an async def would call asyncio.run from the event
# loop's own thread and raise), same contract as the non-daily routes.
@router.get("/arrangement", response_model=ArrangementInventoryResponse)
def arrangement_inventory(request: Request) -> ArrangementInventoryResponse:
    return request.app.state.arrangement.inventory()


@router.post("/arrangement/refresh",
             response_model=ArrangementRefreshResponse)
def arrangement_refresh(request: Request) -> ArrangementRefreshResponse:
    """One pass over every tier file — the UI's Arrange now action (the
    nightly chain arranges per-tier through /orchestration/run)."""
    return request.app.state.arrangement.arrange()
