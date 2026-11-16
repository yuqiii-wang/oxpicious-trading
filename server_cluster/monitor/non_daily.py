"""server_cluster.monitor.non_daily — the non-daily tasks' completeness
checker + weekend backfill driver.

The CONCEPTS come from the orchestration tier files: the non-daily tiers
(:data:`NON_DAILY_TIERS` — downloads monthly/quarterly/semiannually/once,
builds monthly/semiannually/once, strategy once, monitoring occasionally)
ride the nightly chain, but their own cadence guards make off-cadence
runs no-ops (quarterly compositions refresh only at quarter start,
semiannual dividends at half-year boundaries) — so a FAILED cadence
window leaves the task's related data missing for weeks, invisible to
the nightly self-heal. HOW each task runs comes from the same registry
the chain uses: the tier step's module + args, plus the per-task rerun
force args declared in the check config.

The CHECK is grounded in the DAILY DATA IDENTITIES — the universe the
daily pipelines say exists (stats.index_identity / stats.etf_identity /
stats.stock_identity latest-date codes, per-view SQL under
database/sql_views/server_cluster/monitor/non_daily/) compared against
what the non-daily task has loaded (stats.sec_composition snapshots,
stats.stock_dividends freshness, sec_importance_ranks as-of, temps
snapshot files). Configs live in the git-reviewed static tree
database/sql/monitoring/static/non_daily_checks/<dotted.service>.json;
tasks without a config are schedule/last-run monitored only.

The POLICY: a check that finds missing data sets ``next_check_at`` to
the NEXT Saturday 05:00 Asia/Shanghai — the maintainer's
``non_daily_backfill`` job fires then (weekends only, so re-runs never
race the trading-day nightly chain) and re-runs each due task through
this area's run registry (module + tier args + rerun_args — the force
flags that bypass the tasks' own cache guards mid-cadence); a
still-missing task retries ONE WEEK later, until all the data is done
loaded (no attempt cap — ``attempts`` just counts). Every verdict lands
in ``monitor.non_daily_task_state``; every re-run is a real registry
child with its own monitor.service_audit row.

Endpoints (sync ``def`` on purpose — a check pass / re-run drive is
minutes-long blocking work run on the threadpool, like the maintainer's
run endpoints; an async def would wedge the event loop):
 - ``GET  /non-daily``          — the inventory + current state.
 - ``POST /non-daily/evaluate`` — one check pass, verdicts upserted.
 - ``POST /non-daily/backfill`` — the weekend drive (evaluate; re-run
   each due task; re-check; record attempt + next weekly gate).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from _common.db_commons import get_db_connection_async
from _common.labels import NON_DAILY_STATE_CODE
from _common.sql_views import render_sql_view
from _common.sql_writes import render_sql_write
from .data_definitions import (
    NonDailyBackfillRequest, NonDailyBackfillResponse, NonDailyBackfillRerun,
    NonDailyEvaluateResponse, NonDailyInventoryResponse, NonDailyTaskRow)
from .orchestration import OrchestrationRunner

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
CHINA_TZ = ZoneInfo("Asia/Shanghai")

# The check-config static tree (git-reviewed, read-only — no UI write
# path, like external_request_config).
CHECKS_DIR = (_REPO_ROOT / "database" / "sql" / "monitoring" / "static"
              / "non_daily_checks")

# The inventory's tier files (NIGHTLY_CHAIN minus the daily tiers — the
# "concepts" this surface monitors). Kept explicit and deliberate, in
# chain order.
NON_DAILY_TIERS: list[str] = [
    "monitoring_occasionally",
    "downloads_monthly",
    "downloads_quarterly",
    "downloads_semiannually",
    "downloads_once",
    "builds_monthly",
    "builds_semiannually",
    "builds_once",
    "strategy_once",
]

# Tiers whose tasks the weekend backfill may re-run. downloads_once
# (heavy serial seed loads — the hardware-overheat note), strategy_once
# (the one-time backtest, not data loading) and monitoring_occasionally
# (alert-only) are listed in the inventory but never re-run.
RERUN_ELIGIBLE_TIERS: set[str] = {
    "downloads_monthly", "downloads_quarterly", "downloads_semiannually",
    "builds_monthly", "builds_semiannually", "builds_once",
}

# The backfill's fire anchor: Saturday 05:00 China time (the maintainer
# job's own fire time — after the 03:30 maintenance pass, on a
# non-trading day so the re-runs never race the nightly chain).
_BACKFILL_HOUR, _BACKFILL_MINUTE = 5, 0
_RETRY_INTERVAL = timedelta(days=7)

# The missing-detail JSON sample cap (enough to eyeball, never the list).
_DETAIL_SAMPLE = 25

# Registry poll cadence for a re-run's completion (the orchestration
# runner's own step cadence).
_RERUN_POLL_S = 2.0

_STATE_VIEW = "server_cluster/monitor/non_daily/task_state"
_UPSERT_WRITE = ("server_cluster/monitor/non_daily/non_daily_task_state/"
                 "upsert_state")
_RECORD_RERUN_WRITE = ("server_cluster/monitor/non_daily/"
                       "non_daily_task_state/record_rerun")

# Reserved params the checker itself consumes; everything else in a
# config's params is passed to the view renderer as a slot arg.
RESERVED_VIEW_PARAMS = {"view", "max_age_days", "min_bytes", "dir",
                        "pattern", "start"}


class NonDailyCheckConfig(BaseModel):
    """One task's check config file from the static tree."""

    service: str
    check: str = Field(description="db_universe_coverage | "
                        "db_source_freshness | db_asof_current | "
                        "snapshot_fresh | months_present")
    params: dict = Field(default_factory=dict)
    rerun_args: list[str] = Field(default_factory=list)
    notes: str | None = None

    @classmethod
    def load_all(cls) -> dict[str, "NonDailyCheckConfig"]:
        """Every config in the static tree, keyed by service. A
        malformed file is logged and skipped — it must never take the
        inventory down."""
        configs: dict[str, NonDailyCheckConfig] = {}
        for path in sorted(CHECKS_DIR.rglob("*.json")):
            try:
                cfg = cls.model_validate(json.loads(
                    path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                logger.warning("non-daily check config unreadable (%s): "
                               "%s", path.name, exc)
                continue
            configs[cfg.service] = cfg
        return configs


class CheckVerdict(BaseModel):
    """One check's outcome, as upserted into the state table."""

    state: str = Field(description="complete | missing | check_failed "
                        "(label key of the non_daily_state domain)")
    missing_count: int = 0
    missing_detail: dict = Field(default_factory=dict)


def service_tier_map() -> dict[str, str]:
    """service -> its non-daily tier file stem, from the tier files
    (the step's script IS the dotted service name)."""
    mapping: dict[str, str] = {}
    for tier in NON_DAILY_TIERS:
        try:
            plan = OrchestrationRunner.plan(tier)
        except HTTPException:
            logger.warning("non-daily: tier %s unreadable", tier)
            continue
        for step in plan.steps:
            mapping.setdefault(step.script, tier)
    return mapping


class NonDailyOps:
    """The checker + backfill driver (one instance per server, mounted
    on ``app.state.non_daily``; thread-safe — the router resolves the
    instance off app.state)."""

    def __init__(self, registry) -> None:
        self._registry = registry
        self._backfill_lock = threading.Lock()

    # ------------------------------------------------------------------
    # inventory (GET /non-daily)
    # ------------------------------------------------------------------

    def inventory(self) -> NonDailyInventoryResponse:
        """Every non-daily tier's tasks: the orchestration steps merged
        with their check configs and current state rows."""
        configs = NonDailyCheckConfig.load_all()
        states = self._fetch_states()
        rows: list[NonDailyTaskRow] = []
        for tier in NON_DAILY_TIERS:
            try:
                plan = OrchestrationRunner.plan(tier)
            except HTTPException:
                continue  # logged by service_tier_map's pass
            for step in plan.steps:
                cfg = configs.get(step.script)
                state = states.get(step.script, {})
                rows.append(NonDailyTaskRow(
                    service=step.script, tier=tier, step=step.num,
                    script=step.script, args=step.args,
                    check=cfg.check if cfg else None,
                    rerun_args=cfg.rerun_args if cfg else [],
                    rerun_eligible=tier in RERUN_ELIGIBLE_TIERS,
                    state=state.get("state_label", "unknown"),
                    state_code=state.get("state",
                                         NON_DAILY_STATE_CODE["unknown"]),
                    missing_count=state.get("missing_count", 0),
                    missing_detail=state.get("missing_detail"),
                    attempts=state.get("attempts", 0),
                    last_check_at=_iso_or_none(state.get("last_check_at")),
                    last_rerun_at=_iso_or_none(state.get("last_rerun_at")),
                    next_check_at=_iso_or_none(state.get("next_check_at"))))
        return NonDailyInventoryResponse(tasks=rows)

    # ------------------------------------------------------------------
    # evaluate (POST /non-daily/evaluate)
    # ------------------------------------------------------------------

    def evaluate(self) -> NonDailyEvaluateResponse:
        """One check pass over every configured task — verdicts upserted
        into monitor.non_daily_task_state (a passing check resets the
        backfill attempt counter)."""
        configs = NonDailyCheckConfig.load_all()
        verdicts = asyncio.run(self._evaluate_configs(configs))
        counts = {"complete": 0, "missing": 0, "check_failed": 0}
        for verdict in verdicts.values():
            counts[verdict.state] = counts.get(verdict.state, 0) + 1
        logger.info("non-daily evaluate: %d task(s): %s", len(verdicts),
                    ", ".join(f"{k} {v}" for k, v in counts.items()))
        return NonDailyEvaluateResponse(
            evaluated=len(verdicts), complete=counts["complete"],
            missing=counts["missing"],
            check_failed=counts["check_failed"])

    async def _evaluate_configs(
            self, configs: dict[str, NonDailyCheckConfig]
    ) -> dict[str, CheckVerdict]:
        """Run each config's check and upsert its verdict (one shared
        connection; a task's failure is its own check_failed verdict,
        never the pass's)."""
        tiers = service_tier_map()
        verdicts: dict[str, CheckVerdict] = {}
        conn = await get_db_connection_async()
        try:
            for cfg in configs.values():
                verdict = await self._check_one(conn, cfg)
                verdicts[cfg.service] = verdict
                await self._upsert_state(
                    conn, cfg, tiers.get(cfg.service, "unknown"), verdict)
        finally:
            await conn.close()
        return verdicts

    # ------------------------------------------------------------------
    # the checks
    # ------------------------------------------------------------------

    async def _check_one(self, conn,
                         cfg: NonDailyCheckConfig) -> CheckVerdict:
        try:
            if cfg.check == "db_universe_coverage":
                return await self._check_universe(conn, cfg)
            if cfg.check == "db_source_freshness":
                return await self._check_source_fresh(conn, cfg)
            if cfg.check == "db_asof_current":
                return await self._check_asof(conn, cfg)
            if cfg.check == "snapshot_fresh":
                return self._check_snapshot(cfg)
            if cfg.check == "months_present":
                return self._check_months(cfg)
            return CheckVerdict(
                state="check_failed",
                missing_detail={"error": f"unknown check {cfg.check!r}"})
        except Exception as exc:  # noqa: BLE001 — one task's check
            logger.exception(  # failure is its own verdict
                "non-daily check failed for %s", cfg.service)
            return CheckVerdict(
                state="check_failed",
                missing_detail={"error": f"{type(exc).__name__}: {exc}"})

    async def _check_universe(self, conn,
                              cfg: NonDailyCheckConfig) -> CheckVerdict:
        """Identity codes whose target-table coverage is stale/absent —
        the view returns one row per missing code."""
        sql, params = self._render_view(cfg)
        rows = await conn.fetch(sql, *params)
        codes = [str(r["code"]) for r in rows]
        if not codes:
            return CheckVerdict(state="complete")
        return CheckVerdict(
            state="missing", missing_count=len(codes),
            missing_detail={"codes": codes[:_DETAIL_SAMPLE]})

    async def _check_source_fresh(self, conn,
                                  cfg: NonDailyCheckConfig) -> CheckVerdict:
        """The target table's per-source last_updated age vs the
        configured max_age_days window."""
        max_age = float(cfg.params.get("max_age_days", 0))
        sql, params = self._render_view(cfg)
        row = await conn.fetchrow(sql, *params)
        n_rows = int(row["n_rows"] or 0)
        age_days = float(row["age_days"] or 0)
        if n_rows > 0 and age_days <= max_age:
            return CheckVerdict(
                state="complete",
                missing_detail={"n_rows": n_rows,
                                "age_days": round(age_days, 1)})
        return CheckVerdict(
            state="missing", missing_count=1,
            missing_detail={"n_rows": n_rows,
                            "age_days": round(age_days, 1),
                            "max_age_days": max_age})

    async def _check_asof(self, conn,
                          cfg: NonDailyCheckConfig) -> CheckVerdict:
        """A monthly rebuild table's max(as_of) vs the current month."""
        sql, params = self._render_view(cfg)
        row = await conn.fetchrow(sql, *params)
        max_asof = row["max_as_of"]
        month_start = row["month_start"]
        if isinstance(month_start, datetime):
            month_start = month_start.date()  # date_trunc yields timestamp
        if max_asof is not None and max_asof >= month_start:
            return CheckVerdict(
                state="complete",
                missing_detail={"max_as_of": max_asof.isoformat()})
        return CheckVerdict(
            state="missing", missing_count=1,
            missing_detail={
                "max_as_of": max_asof.isoformat() if max_asof else None,
                "month_start": month_start.isoformat()})

    def _check_snapshot(self, cfg: NonDailyCheckConfig) -> CheckVerdict:
        """The newest temps snapshot file: present, big enough, fresh
        enough (mtime age vs max_age_days)."""
        root = _REPO_ROOT / str(cfg.params.get("dir", ""))
        pattern = str(cfg.params.get("pattern", "*"))
        min_bytes = int(cfg.params.get("min_bytes", 1))
        max_age_days = float(cfg.params.get("max_age_days", 0))
        newest: Path | None = None
        newest_mtime: float | None = None
        for path in root.glob(pattern):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size < min_bytes:
                continue
            if newest_mtime is None or stat.st_mtime > newest_mtime:
                newest, newest_mtime = path, stat.st_mtime
        now = time.time()
        if newest is not None and (now - (newest_mtime or 0)) <= (
                max_age_days * 86400.0):
            return CheckVerdict(state="complete")
        detail: dict = {"dir": str(cfg.params.get("dir", ""))}
        if newest is None:
            detail["error"] = "no snapshot file matches"
        else:
            detail["newest"] = newest.name
            detail["age_days"] = round((now - (newest_mtime or 0))
                                       / 86400.0, 1)
            detail["max_age_days"] = max_age_days
        return CheckVerdict(state="missing", missing_count=1,
                            missing_detail=detail)

    def _check_months(self, cfg: NonDailyCheckConfig) -> CheckVerdict:
        """Expected month dirs (start..last completed month) present on
        disk — the CFFEX archive's own range rule (day <= 5 means the
        previous month may still be publishing: two months back)."""
        root = _REPO_ROOT / str(cfg.params.get("dir", ""))
        start = str(cfg.params.get("start", ""))
        year, month = (int(x) for x in start.split("-"))
        last = self._last_completed_month(date.today())
        missing: list[str] = []
        cursor = date(year, month, 1)
        while (cursor.year, cursor.month) <= last:
            if not (root / f"{cursor.year:04d}{cursor.month:02d}").is_dir():
                missing.append(f"{cursor.year:04d}-{cursor.month:02d}")
            cursor = _next_month(cursor)
        if not missing:
            return CheckVerdict(state="complete")
        return CheckVerdict(
            state="missing", missing_count=len(missing),
            missing_detail={"months": missing[:_DETAIL_SAMPLE]})

    @staticmethod
    def _last_completed_month(today: date) -> tuple[int, int]:
        """The last CFFEX-archive-complete month: within the first 5
        days the previous month may still be publishing, so the safe
        target is two months back (the module's own rule)."""
        back = 2 if today.day <= 5 else 1
        cursor = date(today.year, today.month, 1)
        for _ in range(back):
            cursor = _prev_month(cursor)
        return cursor.year, cursor.month

    @staticmethod
    def _render_view(cfg: NonDailyCheckConfig) -> tuple[str, list]:
        """(sql, params) for the config's view — every non-reserved
        param rides the renderer as a slot arg (grace_days, exchange,
        source, ...)."""
        view = render_sql_view(str(cfg.params["view"]), **{
            key: value for key, value in cfg.params.items()
            if key not in RESERVED_VIEW_PARAMS})
        return view.sql, list(view.params)

    # ------------------------------------------------------------------
    # state persistence
    # ------------------------------------------------------------------

    async def _upsert_state(self, conn, cfg: NonDailyCheckConfig,
                            tier: str, verdict: CheckVerdict) -> None:
        write = render_sql_write(
            _UPSERT_WRITE, cfg.service, tier,
            NON_DAILY_STATE_CODE[verdict.state], cfg.check,
            verdict.missing_count, json.dumps(verdict.missing_detail),
            (self._next_check_at() if verdict.state == "missing"
             else None))
        await conn.execute(write.sql, *write.params)

    async def _record_rerun(self, conn, service: str) -> None:
        write = render_sql_write(_RECORD_RERUN_WRITE, service,
                                 self._next_check_at())
        await conn.execute(write.sql, *write.params)

    def _fetch_states(self) -> dict[str, dict]:
        """The current state rows, keyed by service (sync face over the
        shared view — the inventory + backfill reads)."""
        return asyncio.run(self._fetch_states_async())

    async def _fetch_states_async(self) -> dict[str, dict]:
        view = render_sql_view(_STATE_VIEW)
        conn = await get_db_connection_async()
        try:
            rows = await conn.fetch(view.sql, *view.params)
        finally:
            await conn.close()
        states: dict[str, dict] = {}
        for row in rows:
            state = dict(row)
            detail = state.get("missing_detail")
            state["missing_detail"] = (json.loads(detail)
                                       if isinstance(detail, str)
                                       else detail)
            states[str(state["service"])] = state
        return states

    # ------------------------------------------------------------------
    # backfill (POST /non-daily/backfill)
    # ------------------------------------------------------------------

    def backfill(self, req: NonDailyBackfillRequest
                 ) -> NonDailyBackfillResponse:
        """The weekend drive: evaluate every task, then re-run each DUE
        missing re-run-eligible task (state missing, next_check_at
        arrived) through the run registry — module + tier step args +
        rerun_args — poll it to its exit, re-check the task, and record
        the attempt + the next weekly gate. ONE drive at a time."""
        if not self._backfill_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail="a non-daily backfill drive is already running")
        try:
            return self._backfill_locked(req)
        finally:
            self._backfill_lock.release()

    def _backfill_locked(self, req: NonDailyBackfillRequest
                         ) -> NonDailyBackfillResponse:
        configs = NonDailyCheckConfig.load_all()
        asyncio.run(self._evaluate_configs(configs))
        states = self._fetch_states()
        now = datetime.now(CHINA_TZ)
        due: list[tuple[str, dict]] = []
        for service, state in sorted(states.items()):
            if state.get("state_label") != "missing":
                continue
            if state.get("tier") not in RERUN_ELIGIBLE_TIERS:
                continue
            due_at = _as_shanghai(state.get("next_check_at"))
            if due_at is not None and due_at <= now:
                due.append((service, state))
        reruns: list[NonDailyBackfillRerun] = []
        for service, _state in due:
            step = self._tier_step(service)
            if step is None:
                logger.warning("non-daily backfill: %s not found in its "
                               "tier file — skipping", service)
                continue
            cfg = configs.get(service)
            args = list(step.args) + (list(cfg.rerun_args)
                                      if cfg else [])
            record = NonDailyBackfillRerun(
                service=service, tier=step.tier, module=step.script,
                args=args, dry_run=req.dry_run)
            if not req.dry_run:
                record.submitted, record.exit_code = self._drive_rerun(
                    service, step.script, args)
                record.state_after = self._recheck_one(configs, service)
            reruns.append(record)
        detail = (f"{len(due)} due task(s), "
                  f"{'dry run' if req.dry_run else f'{len(reruns)} driven'}"
                  f": {', '.join(r.service for r in reruns) or 'none'}")
        logger.info("non-daily backfill: %s", detail)
        return NonDailyBackfillResponse(
            evaluated=len(configs),
            missing=sum(1 for s in states.values()
                        if s.get("state_label") == "missing"),
            due=[service for service, _ in due], reruns=reruns,
            detail=detail)

    def _drive_rerun(self, service: str, module: str,
                     args: list[str]) -> tuple[bool, int | None]:
        """Submit one re-run through the run registry and poll it to its
        exit (registry dedupe + per-service max_parallel + the module's
        single-instance lock all still apply). Records the attempt +
        next weekly gate whatever the exit. Returns (submitted,
        exit_code)."""
        tag = (f"non-daily:{service}:"
               f"{datetime.now(CHINA_TZ).strftime('%Y%m%d')}")
        try:
            entry, already = self._registry.submit(module, args, tag)
        except Exception:  # noqa: BLE001 — one task's submit failure
            logger.exception(  # must not abort the drive
                "non-daily backfill: submit failed for %s", service)
            return False, None
        if already:
            logger.info("non-daily backfill: %s deduped to an in-flight "
                        "run (%s)", service, entry.tag)
        code: int | None = None
        while True:
            status = self._registry.status_many([entry.tag]).get(entry.tag)
            if status is None or not status.get("running", True):
                code = status.get("exit_code") if status else None
                break
            time.sleep(_RERUN_POLL_S)
        asyncio.run(self._record_rerun_async(service))
        logger.info("non-daily backfill: %s rerun exit %s", service, code)
        return True, code

    async def _record_rerun_async(self, service: str) -> None:
        conn = await get_db_connection_async()
        try:
            await self._record_rerun(conn, service)
        finally:
            await conn.close()

    def _recheck_one(self, configs: dict[str, NonDailyCheckConfig],
                     service: str) -> str:
        cfg = configs.get(service)
        if cfg is None:
            return "unknown"
        verdicts = asyncio.run(self._evaluate_configs({service: cfg}))
        return verdicts[service].state

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tier_step(service: str):
        """The service's (tier, step) from the non-daily tier files —
        the module + args the backfill re-runs. None when absent."""

        class _TierStep(BaseModel):
            tier: str
            script: str
            args: list[str] = Field(default_factory=list)

        for tier in NON_DAILY_TIERS:
            try:
                plan = OrchestrationRunner.plan(tier)
            except HTTPException:
                continue
            for step in plan.steps:
                if step.script == service:
                    return _TierStep(tier=tier, script=step.script,
                                     args=list(step.args))
        return None

    @staticmethod
    def _next_check_at() -> datetime:
        """The next Saturday 05:00 Asia/Shanghai STRICTLY after now —
        the missing-state re-check gate; the drive itself bumps it +7d
        per attempt (the weekly retry interval)."""
        now = datetime.now(CHINA_TZ)
        days_ahead = (5 - now.weekday()) % 7  # Saturday == 5
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=_BACKFILL_HOUR, minute=_BACKFILL_MINUTE, second=0,
            microsecond=0)
        if candidate <= now:
            candidate += _RETRY_INTERVAL
        return candidate


def _next_month(cursor: date) -> date:
    year, month = cursor.year, cursor.month + 1
    if month > 12:
        year, month = year + 1, 1
    return date(year, month, 1)


def _prev_month(cursor: date) -> date:
    year, month = cursor.year, cursor.month - 1
    if month < 1:
        year, month = year - 1, 12
    return date(year, month, 1)


def _as_shanghai(value) -> datetime | None:
    """A state-row timestamptz as a Shanghai-aware datetime (asyncpg
    returns aware UTC datetimes; a naive one is treated as UTC)."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso_or_none(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


router = APIRouter(tags=["non-daily"])


@router.get("/non-daily", response_model=NonDailyInventoryResponse)
def non_daily_inventory(request: Request) -> NonDailyInventoryResponse:
    return request.app.state.non_daily.inventory()


@router.post("/non-daily/evaluate",
             response_model=NonDailyEvaluateResponse)
def non_daily_evaluate(request: Request) -> NonDailyEvaluateResponse:
    return request.app.state.non_daily.evaluate()


@router.post("/non-daily/backfill", response_model=NonDailyBackfillResponse)
def non_daily_backfill(request: Request,
                       req: NonDailyBackfillRequest | None = None
                       ) -> NonDailyBackfillResponse:
    return request.app.state.non_daily.backfill(
        req or NonDailyBackfillRequest())
