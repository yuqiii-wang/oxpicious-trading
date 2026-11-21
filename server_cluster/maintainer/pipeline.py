"""server_cluster.maintainer.pipeline — the nightly pipeline chain.

MIGRATED from the sh tier (``main_run.sh`` → ``scripts/<tier>.sh`` →
``scripts/run_steps.sh``, retired 2026-11): the schedule now belongs
to the maintainer — this job is a fire group of its own on the shared
:class:`~server_cluster.maintainer.scheduler.MaintenanceScheduler`
(default 16:00 Asia/Shanghai on every TRADING day — the whole pass
skips on non-trading days. The 2026-11-16..20 16:00 fires aborted at
the then-6h tier cap — since fixed by the downloads_daily 11h
override — and the sh-guard-inherited 18:00 start had been kept to
stay past the CSIndex quote sweep's ``EOD_EXPECT_HOUR = 17`` publish
gate; moved to 16:00 on 2026-11-20 by the owner's call, ACCEPTING
the gate's cost: before 17:00 the sweep expects the PREVIOUS
session, so the CSI-sourced close-quote frontier lands one trading
day late (the 2026-10-17 partial-frontier study's shape). The pacing
itself fits — the ~8.5h sweep sits inside the 11h cap: tier done
~00:30-01:00, whole chain ~02:30-03:00, before the 09:30 open).
EXECUTION
stays where the UI already drives it: each tier file under
``scripts/orchestration/`` runs serially through the monitor area's
OrchestrationRunner (``POST /orchestration/run`` — a real
``python -m`` child per step, run_steps semantics: step failures are
counted, never fatal to the tier).

Per tier the job ADOPTS an already-in-flight run of the same file (the
old per-tier flock await: a manual or UI-triggered run is waited out,
never raced), polls the run to its exit, and moves on. A tier that
exceeds the wait cap, or whose run the monitor loses TWICE (a monitor
restart wipes its in-memory run table — the FIRST loss recovers by
re-adopting or re-starting the tier), ABORTS the chain — dependents
never run on partial inputs.

Recovery surface (what the 21:00 check — or an operator — drives):
 - ``GET  /pipeline/last-pass`` — the last pass's persisted record:
   per-tier outcomes, what remains, whether a chain is running now.
 - ``POST /pipeline/finish`` — finish the last pass's REMAINING tiers
   (failed + never-reached, chain order, gates off).
 - ``POST /jobs/nightly_pipeline/run`` with ``{"tiers": [...],
   "force": true}`` — rerun PARTICULAR tiers (empty ``tiers`` = the
   whole chain; ``force`` bypasses the trading-day gate — the old
   ``FORCE_DOWNLOADS=1``).

State: ``server_cluster/_state/nightly_pipeline.json``. Written as
tiers complete; an INTERRUPTED pass (maintainer died mid-chain — the
monitor revives it within one supervisor pass) resumes from the first
incomplete tier on startup. An ENDED pass with failures KEEPS its
record (``ended: "failed" | "aborted"``) as the finish endpoint's
worklist; a clean pass deletes the file. One ``monitor.service_audit``
row records every pass.

``MAINTAINER_PIPELINE_AT`` (env, ``HH:MM`` China time) overrides the
fire time for testing; ``MAINTAINER_PIPELINE_TIER_TIMEOUT_S`` (env,
seconds) the per-tier wait cap (default 6h;
:data:`TIER_TIMEOUT_S_OVERRIDES` widens downloads_daily).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from _common._holidays_and_weekdays import is_trading_day
from _common.caution_log import record_service_error
from _common.internal_request import InternalRequestError, request_json
from server_cluster.maintainer.bring_up import monitor_base_url
from server_cluster.maintainer.jobs import (JobResult, MaintenanceJob,
                                            PassAudit)

logger = logging.getLogger(__name__)

CHINA_TZ = ZoneInfo("Asia/Shanghai")

# The fire time (China local) and the audit trail constants.
DEFAULT_FIRE_AT = "16:00"
FIRE_AT_ENV = "MAINTAINER_PIPELINE_AT"
TIER_TIMEOUT_ENV = "MAINTAINER_PIPELINE_TIER_TIMEOUT_S"
DEFAULT_TIER_TIMEOUT_S = 6 * 3600
# Per-tier wait-cap overrides, seconds. downloads_daily carries the
# CSIndex full-universe sweep (~510 codes at the deliberate anti-bot
# pacing — ~8.5h observed 2026-11-17..20, plus the other waves), which
# the 6h default can NEVER fit: every 2026-11-16..20 chain aborted at
# the cap with builds/analysis never reached. 11h from the 16:00 fire
# leaves the tier done ~00:30-01:00 and the whole chain ~02:30-03:00,
# still before the 09:30 open. The env override (testing) wins over
# both.
TIER_TIMEOUT_S_OVERRIDES: dict[str, float] = {
    "downloads_daily": 11.0 * 3600.0,
}
AUDIT_SERVICE = "server_cluster.maintainer.pipeline"
AUDIT_EXIT_REASON = "nightly pipeline chain"

_TIER_POLL_S = 30.0
_REQUEST_TIMEOUT_S = 15.0

_STATE_DIR = Path(__file__).resolve().parents[1] / "_state"
_STATE_FILE = _STATE_DIR / "nightly_pipeline.json"

# The canonical chain — tier file stems under scripts/orchestration/,
# in run order (the display mirrors: the Express API's TIER_RUN_ORDER,
# scripts/orchestration/README.md). The WHOLE pass is trading-day
# gated; the tiers' own incremental guards make off-cadence runs
# no-ops (monthly tasks refresh on the 1st, quarterly on the 1st of a
# quarter-start month), so nothing else needs a per-tier gate.
NIGHTLY_CHAIN: list[str] = [
    # monitoring first — alert-only (replica lag, WAL pinning) and
    # never blocks the scheduled work
    "monitoring_occasionally",
    "downloads_daily",
    # the weekly SSE stock-PE anchor tier rides every pass; its own
    # weekly gate (WEEKLY_GATE_DAYS in downloads.stock.sse.trend_pe)
    # makes off-cadence fires no-ops (the analysis_weekly precedent)
    "downloads_weekly",
    "downloads_monthly",
    "downloads_quarterly",
    "downloads_semiannually",
    "downloads_once",
    "builds_daily",
    "builds_semiannually",
    "builds_once",
    # monthly importance ranks run AFTER the daily/once builds whose
    # turnover outputs feed them (the build's monthly guard makes
    # non-first runs of the month a no-op)
    "builds_monthly",
    "analysis_daily",
    # the weekly hindsight-oracle tier rides every pass; its own weekly
    # gate (config.WEEKLY_GATE_DAYS in analysis.oracle_signals) makes
    # off-cadence fires no-ops (the builds_monthly month-guard
    # precedent)
    "analysis_weekly",
    # the nightly singleton strategy (all-families portfolio, comb on)
    # runs AFTER the analysis tiers that produce its signal history
    "strategy_daily",
    "strategy_once",
]


class PipelineRunRequest(BaseModel):
    """A chain pass's scope — the manual-run body; the scheduled fire
    uses the default (the whole chain, gates on)."""

    tiers: list[str] = Field(default_factory=list, description="Tier "
                             "files to run (kept in CHAIN order however "
                             "listed); empty = the whole NIGHTLY_CHAIN.")
    force: bool = Field(default=False, description="Bypass the "
                        "trading-day gate (the old FORCE_DOWNLOADS=1).")


class TierOutcome(BaseModel):
    """One tier's result within a chain pass."""

    file: str
    state: str            # ok | failed | not_run
    failed_steps: int = 0
    detail: str = ""


class ChainState(BaseModel):
    """The on-disk pass record under ``server_cluster/_state/`` —
    written as tiers complete. A clean pass deletes it; a pass with
    failures keeps it (``ended`` set) as the finish endpoint's
    worklist; an interrupted pass (``ended`` None) is the startup
    resume's charge."""

    date: str          # China-local YYYY-MM-DD of the pass
    started_at: str    # China-local ISO timestamp
    tiers: list[str]   # the pass's tier files, in run order
    completed: list[str] = Field(default_factory=list,
                                 description="Tiers whose outcome was "
                                 "ok.")
    outcomes: list[TierOutcome] = Field(default_factory=list,
                                        description="The full per-tier "
                                        "record, written at pass end.")
    ended: str | None = Field(default=None, description="None = in "
                              "flight / interrupted; else ok's pass file "
                              'is deleted — "failed" or "aborted" stay.')


class PipelineLastPass(BaseModel):
    """The recovery surface's read: the last pass's record plus the
    live running flag (the 21:00 check and operators decide off
    this)."""

    running: bool = Field(description="A chain pass is executing right "
                          "now (the finish/run endpoints would refuse).")
    exists: bool = Field(default=False, description="A pass state file "
                         "exists — false after a clean pass or before "
                         "today's fire.")
    date: str | None = None
    started_at: str | None = None
    ended: str | None = None
    tiers: list[TierOutcome] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list,
                                 description="Tiers not yet ok — the "
                                 "finish endpoint's worklist.")


class InFlightRun(BaseModel):
    """One in-flight orchestration run (the monitor's adoption view)."""

    tag: str
    file: str


class TierStepStatus(BaseModel):
    """One step within a tier run (the runner's per-step record)."""

    num: str
    script: str
    state: str            # pending | running | done | failed
    tag: str | None = None
    exit_code: int | None = None


class TierRunAccepted(BaseModel):
    """A started (or adopted) tier run."""

    tag: str
    file: str


class TierRunStatus(BaseModel):
    """A tier run's polled status."""

    tag: str
    file: str
    state: str            # running | done | failed
    steps: list[TierStepStatus] = Field(default_factory=list)


class MonitorOrchestrationError(RuntimeError):
    """The monitor area's orchestration surface failed the chain
    (unreachable, bad answer, run lost, or wait cap exceeded)."""


class RunConflictError(MonitorOrchestrationError):
    """A second run of the same tier file was refused (409) — the
    chain adopts the in-flight run instead."""


class RunLostError(MonitorOrchestrationError):
    """The monitored run's status vanished (404) — a monitor restart
    wiped its in-memory run table; the chain must not continue on
    partial inputs."""


class MonitorOrchestrationClient:
    """The monitor area's ``/orchestration`` face (requests ride
    :mod:`_common.internal_request` — stdlib urllib under the hood, the
    fleet's no-new-dependency rule): list the in-flight tier runs,
    start one, poll one run's status. The HTTP statuses that carry
    chain semantics map to this module's typed errors here; everything
    else is the ONE generic orchestration failure."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url

    def in_flight(self) -> list[InFlightRun]:
        doc = self._request("GET", "/orchestration/in-flight")
        return [InFlightRun.model_validate(r)
                for r in doc.get("runs", [])]

    def start(self, file: str) -> TierRunAccepted:
        """Start one tier run ARRANGED: the monitor area first upserts
        the tier's freq-window arrangement
        (monitor.orchestration_task_state — the Shanghai-calendar rule
        over each step's service_config ``freq`` vs its last successful
        exit) and then skips the not-due steps (state skipped_not_due)
        instead of spawning them to internally no-op off-cadence — the
        maintainer arranges what to run, the Monitor UI's Orchestration
        page shows the arrangement."""
        doc = self._request("POST", "/orchestration/run",
                            {"file": file, "arrange": True})
        return TierRunAccepted.model_validate(doc)

    def status(self, tag: str) -> TierRunStatus:
        doc = self._request("GET", f"/orchestration/run/{tag}")
        return TierRunStatus.model_validate(doc)

    def _request(self, method: str, path: str,
                 body: dict | None = None) -> list | dict:
        url = f"{self._base_url or monitor_base_url()}{path}"
        try:
            return request_json(method, url, json_body=body,
                                timeout_s=_REQUEST_TIMEOUT_S)
        except InternalRequestError as exc:
            if exc.status_code == 409:
                raise RunConflictError(str(exc)) from exc
            if exc.status_code == 404 and method == "GET":
                raise RunLostError(str(exc)) from exc
            raise MonitorOrchestrationError(str(exc)) from exc


class NightlyPipelineJob(MaintenanceJob):
    """The nightly tier chain — one serial pass over
    :data:`NIGHTLY_CHAIN` (or the requested subset), each tier driven
    through the monitor area's orchestration runner over HTTP and
    polled to its exit. Own fire group on the shared scheduler
    (default 16:00 China time, trading days only)."""

    name = "nightly_pipeline"
    description = ("Nightly 16:00 (trading-day) pipeline chain: "
                   "monitoring → downloads → builds → analysis → "
                   "strategy tier files, run serially through the "
                   "monitor area's /orchestration runner (run_steps "
                   "semantics — step failures counted, never fatal; a "
                   "timed-out tier, or one the monitor loses twice, "
                   "aborts the chain). "
                   "Non-trading days skip the whole pass; an "
                   "interrupted pass resumes on restart, a failed one "
                   "keeps its record for POST /pipeline/finish.")

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__()
        self._client = MonitorOrchestrationClient(base_url)
        self._chain_lock = threading.Lock()
        self._request = PipelineRunRequest()
        self.fire_at = os.environ.get(FIRE_AT_ENV, DEFAULT_FIRE_AT)

    # ------------------------------------------------------------------
    # the guarded pass
    # ------------------------------------------------------------------

    def run(self, request: PipelineRunRequest | None = None) -> JobResult:
        """One guarded chain pass — ONE chain at a time (the old
        per-tier flock's whole-chain equivalent: a second concurrent
        chain is refused with ``ok=False``, never queued)."""
        if not self._chain_lock.acquire(blocking=False):
            return JobResult(
                job=self.name,
                started_at=datetime.now(timezone.utc),
                duration_s=0.0, ok=False,
                detail="a chain pass is already running — refusing a "
                       "second one")
        self._request = request or PipelineRunRequest()
        try:
            return super().run()
        finally:
            self._request = PipelineRunRequest()
            self._chain_lock.release()

    def _run(self) -> tuple[list[str], str]:
        started = datetime.now(timezone.utc)
        tiers = self._resolve_tiers()
        if not self._request.force and not self._is_trading_day():
            detail = (f"non-trading day — pass skipped "
                      f"({len(tiers)} tier(s) held; force bypasses)")
            logger.info("nightly pipeline: %s", detail)
            PassAudit().record(AUDIT_SERVICE, started, True,
                               AUDIT_EXIT_REASON)
            return [], detail
        self._write_state(tiers)
        outcomes: list[TierOutcome] = []
        aborted: str | None = None
        for tier in tiers:
            try:
                outcome = self._run_tier(tier)
            except MonitorOrchestrationError as exc:
                aborted = f"{tier}: {exc}"
                outcome = TierOutcome(
                    file=tier, state="failed",
                    detail=f"chain aborted — {exc}")
                logger.error("nightly pipeline: chain ABORTED at %s",
                             aborted)
            outcomes.append(outcome)
            if outcome.state == "ok":
                self._mark_completed(tier)
            if aborted is not None:
                break
        for tier in tiers[len(outcomes):]:
            outcomes.append(TierOutcome(
                file=tier, state="not_run",
                detail="chain aborted at " + aborted if aborted else ""))
        detail = self._summary(outcomes)
        ok = (aborted is None
              and not any(o.state == "failed" for o in outcomes))
        self._end_state(outcomes, "aborted" if aborted else "failed")
        PassAudit().record(AUDIT_SERVICE, started, ok, AUDIT_EXIT_REASON)
        logger.info("nightly pipeline pass: %s", detail)
        if not ok:
            raise RuntimeError(detail)
        return [], detail

    def _resolve_tiers(self) -> list[str]:
        """The pass's tiers — the requested subset kept in CHAIN order,
        or the whole chain. Unknown names fail the pass fast."""
        if not self._request.tiers:
            return list(NIGHTLY_CHAIN)
        requested = set(self._request.tiers)
        unknown = sorted(requested.difference(NIGHTLY_CHAIN))
        if unknown:
            raise ValueError(
                f"unknown tier(s) {unknown} — known: {NIGHTLY_CHAIN}")
        return [tier for tier in NIGHTLY_CHAIN if tier in requested]

    @staticmethod
    def _known_tiers(tiers: list[str], context: str) -> list[str]:
        """State-file tiers filtered to the known chain, chain order —
        a tier RENAMED since the state file was written (the 2026-11-16
        'downloads_seasonally' → 'downloads_quarterly' rename bricked
        that night's resume with a ValueError) must degrade to a
        warning, never block recovery. The manual-run path keeps the
        strict :meth:`_resolve_tiers` typo fail-fast."""
        unknown = [t for t in tiers if t not in NIGHTLY_CHAIN]
        if unknown:
            logger.warning("nightly pipeline: dropping unknown tier(s) "
                           "%s from the %s", unknown, context)
        known = set(tiers)
        return [t for t in NIGHTLY_CHAIN if t in known]

    @staticmethod
    def _is_trading_day() -> bool:
        """Today an A-share trading day? (the CN holiday table read;
        fail-closed like the old sh guard — an unreadable table skips
        the pass, it never unleashes it on a holiday)."""
        try:
            return is_trading_day(datetime.now(CHINA_TZ).date())
        except Exception:  # noqa: BLE001
            logger.exception("trading-day check failed — failing closed")
            return False

    def _run_tier(self, tier: str) -> TierOutcome:
        """Drive one tier: adopt an already-in-flight run of the same
        file (a manual / UI-triggered run — waited out, never raced),
        else start one, then poll it to its exit under the wait cap. A
        run LOST mid-poll (monitor restart wiped its in-memory table)
        is recovered ONCE by re-adopting or re-starting the tier; a
        second loss aborts the chain (the monitor is too unstable to
        trust a completion signal from)."""
        tag = self._acquire_run(tier)
        timeout_s = self._tier_timeout_s(tier)
        deadline = time.monotonic() + timeout_s
        lost_once = False
        while True:
            try:
                status = self._client.status(tag)
            except RunLostError as exc:
                if lost_once:
                    raise
                lost_once = True
                recovered = self._recover_lost_run(tier, exc)
                if recovered is None:
                    raise
                tag = recovered
                continue
            if status.state != "running":
                break
            if time.monotonic() >= deadline:
                raise MonitorOrchestrationError(
                    f"{tier} still running after "
                    f"{timeout_s:.0f}s wait cap")
            time.sleep(_TIER_POLL_S)
        failed_steps = sum(1 for s in status.steps if s.state == "failed")
        if status.state == "failed" or failed_steps:
            # The per-tier failure is a caution too, with the failed step
            # names + exit codes (the pass summary counts only; the
            # runner's own per-step cautions land under each step's
            # service). Deduped per tier — a recurring bad tier bumps
            # one open row (monitor.caution_log, the Cautions tab).
            failed = [s for s in status.steps if s.state == "failed"]
            failed_names = ", ".join(s.script for s in failed)
            record_service_error(
                f"nightly pipeline tier {tier} failed "
                f"({status.state}, {len(failed)} step(s): {failed_names})",
                dedupe_key=f"tier-failed:{tier}",
                detail={
                    "tier": tier,
                    "run_state": status.state,
                    "failed_steps": [
                        {
                            "num": s.num,
                            "script": s.script,
                            "state": s.state,
                            "exit_code": s.exit_code,
                        }
                        for s in failed
                    ],
                },
                service="server_cluster.maintainer",
            )
            logger.warning("nightly pipeline: %s %s (%d step(s) failed)",
                           tier, status.state, failed_steps)
        return TierOutcome(
            file=tier,
            state="ok" if status.state == "done" else "failed",
            failed_steps=failed_steps,
            detail=f"{len(status.steps)} step(s)")

    def _adopt_in_flight(self, file: str) -> str | None:
        """The in-flight run tag for ``file``, if any — the flock-await
        replacement (a same-file run anywhere is waited out here)."""
        for run in self._client.in_flight():
            if run.file == file:
                return run.tag
        return None

    def _acquire_run(self, tier: str) -> str:
        """Adopt an already-in-flight run of ``tier``'s file, else start
        one — the entry and the lost-run recovery share this contract
        (a 409 on start means somebody raced us to it: adopt theirs)."""
        tag = self._adopt_in_flight(tier)
        if tag is not None:
            logger.info("nightly pipeline: %s adopting in-flight run %s",
                        tier, tag)
            return tag
        try:
            tag = self._client.start(tier).tag
            logger.info("nightly pipeline: %s started (%s)", tier, tag)
            return tag
        except RunConflictError:
            tag = self._adopt_in_flight(tier)
            if tag is None:
                raise
            logger.info("nightly pipeline: %s adopting in-flight run %s "
                        "(409)", tier, tag)
            return tag

    def _recover_lost_run(self, tier: str,
                          lost: RunLostError) -> str | None:
        """One recovery attempt after the polled run vanished (404 — a
        monitor restart wiped its in-memory run table; the run's child
        processes died with it, or keep running unpolled as orphans the
        409 path below guards against re-racing). Re-adopt the file's
        in-flight run if one exists, else start a fresh one — tiers are
        incremental, so a partial pre-restart pass only re-does missing
        work. Returns the new tag, or None when recovery itself failed
        (caller aborts the chain)."""
        logger.warning("nightly pipeline: %s run lost (%s) — recovering "
                       "by re-adopt/re-start", tier, lost)
        record_service_error(
            f"nightly pipeline tier {tier} run lost by monitor restart "
            f"— recovering once by re-adopt/re-start",
            dedupe_key=f"tier-runlost:{tier}",
            detail={"tier": tier, "lost": str(lost)},
            service="server_cluster.maintainer",
        )
        try:
            return self._acquire_run(tier)
        except MonitorOrchestrationError as exc:
            logger.error("nightly pipeline: %s lost-run recovery failed "
                         "(%s)", tier, exc)
            return None

    @staticmethod
    def _summary(outcomes: list[TierOutcome]) -> str:
        ok_n = sum(1 for o in outcomes if o.state == "ok")
        failed = [o for o in outcomes if o.state == "failed"]
        not_run = [o.file for o in outcomes if o.state == "not_run"]
        parts = [f"{len(outcomes)} tier(s): {ok_n} ok"]
        if failed:
            parts.append("; failed: " + ", ".join(
                f"{o.file} ({o.failed_steps} step(s))" for o in failed))
        if not_run:
            parts.append("; not run (chain aborted): "
                         + ", ".join(not_run))
        return "".join(parts)

    @staticmethod
    def _tier_timeout_s(tier: str) -> float:
        raw = os.environ.get(TIER_TIMEOUT_ENV)
        if raw is not None:
            try:
                return float(raw)
            except ValueError:
                logger.warning("%s=%r unreadable — using tier default",
                               TIER_TIMEOUT_ENV, raw)
        return TIER_TIMEOUT_S_OVERRIDES.get(
            tier, float(DEFAULT_TIER_TIMEOUT_S))

    # ------------------------------------------------------------------
    # the recovery surface (the 21:00 check / operators)
    # ------------------------------------------------------------------

    def last_pass(self) -> PipelineLastPass:
        """The last pass's record + the live running flag. ``exists``
        false after a clean pass (file deleted) or before any pass."""
        running = self._chain_lock.locked()
        record = self._read_state()
        if record is None:
            return PipelineLastPass(running=running, exists=False)
        remaining = [tier for tier in record.tiers
                     if tier not in record.completed]
        return PipelineLastPass(
            running=running, exists=True, date=record.date,
            started_at=record.started_at, ended=record.ended,
            tiers=record.outcomes, remaining=remaining)

    def finish_incomplete(self) -> JobResult:
        """Finish the last pass's REMAINING tiers (failed +
        never-reached, chain order, gates off) — the on-demand
        catch-up the 21:00 check or an operator fires after a
        failed / aborted / interrupted pass."""
        record = self._read_state()
        if record is None:
            return JobResult(
                job=self.name,
                started_at=datetime.now(timezone.utc),
                duration_s=0.0, ok=True,
                detail="no pass state on disk — nothing to finish")
        remaining = self._known_tiers(
            [tier for tier in record.tiers
             if tier not in record.completed],
            f"{record.date} pass state")
        if not remaining:
            self._clear_state()
            return JobResult(
                job=self.name,
                started_at=datetime.now(timezone.utc),
                duration_s=0.0, ok=True,
                detail=f"pass of {record.date} has no remaining "
                       f"tiers — state cleared")
        logger.info("nightly pipeline: finishing remaining tier(s) of "
                    "the %s pass: %s", record.date, ", ".join(remaining))
        return self.run(PipelineRunRequest(tiers=remaining, force=True))

    # ------------------------------------------------------------------
    # pass state (crash resume + the finish worklist)
    # ------------------------------------------------------------------

    def resume_if_incomplete(self) -> None:
        """Startup hook: if TODAY's pass was interrupted mid-flight
        (state file survives with ``ended`` unset — every normal pass
        end sets or deletes it), continue it from the first incomplete
        tier on a daemon thread. An ENDED pass (failed/aborted) is the
        finish endpoint's domain, not startup's — a timed-out tier
        must not be auto-retried blind."""
        record = self._read_state()
        if record is None or record.ended is not None:
            return
        today = datetime.now(CHINA_TZ).date().isoformat()
        if record.date != today:
            logger.info("nightly pipeline: dropping stale pass state "
                        "from %s", record.date)
            self._clear_state()
            return
        remaining = self._known_tiers(
            [t for t in record.tiers if t not in record.completed],
            f"{record.date} interrupted pass")
        if not remaining:
            self._clear_state()
            return
        logger.info("nightly pipeline: resuming interrupted pass — %d "
                    "tier(s) remain: %s", len(remaining),
                    ", ".join(remaining))
        threading.Thread(
            target=self._resume_pass, args=(remaining,),
            name="cluster-maintainer-pipeline-resume",
            daemon=True).start()

    def _resume_pass(self, remaining: list[str]) -> None:
        try:
            result = self.run(PipelineRunRequest(
                tiers=remaining, force=True))
            logger.info("nightly pipeline resume: %s (ok=%s)",
                        result.detail, result.ok)
        except Exception:  # noqa: BLE001 — the resume thread reports
            logger.exception("nightly pipeline resume failed")  # only

    def _write_state(self, tiers: list[str]) -> None:
        self._atomic_write(ChainState(
            date=datetime.now(CHINA_TZ).date().isoformat(),
            started_at=datetime.now(CHINA_TZ).isoformat(),
            tiers=list(tiers)))

    def _mark_completed(self, file: str) -> None:
        record = self._read_state()
        if record is not None and file not in record.completed:
            record.completed.append(file)
            self._atomic_write(record)

    def _end_state(self, outcomes: list[TierOutcome],
                   failed_state: str) -> None:
        """A clean pass deletes its record; a pass with failures keeps
        it (outcomes + ended) as the finish endpoint's worklist."""
        record = self._read_state()
        if record is None:
            return
        if not any(o.state != "ok" for o in outcomes):
            self._clear_state()
            return
        record.outcomes = outcomes
        record.ended = failed_state
        self._atomic_write(record)

    def _clear_state(self) -> None:
        try:
            _STATE_FILE.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("nightly pipeline state clear failed: %s", exc)

    @staticmethod
    def _read_state() -> ChainState | None:
        try:
            return ChainState.model_validate(json.loads(
                _STATE_FILE.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("nightly pipeline state unreadable (%s) — "
                           "treating as none", exc)
            return None

    @staticmethod
    def _atomic_write(record: ChainState) -> None:
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(
                json.dumps(record.model_dump(), indent=2) + "\n",
                encoding="utf-8")
        except OSError as exc:
            logger.warning("nightly pipeline state write failed: %s", exc)
