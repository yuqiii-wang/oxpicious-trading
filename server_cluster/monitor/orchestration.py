"""server_cluster.monitor.orchestration — the tier runner + router.

The server-side shape of ``scripts/run_steps.sh``: one orchestration
file's steps run in numeric key order, each submitted through this
area's run registry (the shared jobs surface — so status polling, log
tails, and POST /jobs/{tag}/stop all work for free) and POLLED to
completion. Step failures are counted, never fatal to the tier
(run_steps.sh semantics: monitoring is alert-only; a failed step just
marks the tier failed). The runner's states live in memory (one server,
in-flight tiers only — the durable run trail is the registry's + each
step's own service_audit rows).

ARRANGEMENT (the maintainer-arranged run path): a run posted with
``arrange`` first upserts the tier's freq-window verdicts through the
area's OrchestrationArranger (server_cluster/monitor/arrangement.py —
the Shanghai-calendar rule over the service_config ``freq`` and the
last successful exit) and then SKIPS the not-due steps (state
``skipped_not_due``, never spawned) instead of relying on each step's
in-module cadence guard to no-op — the maintainer's nightly chain
arranges what to run, the Monitor UI's Orchestration page shows the
arrangement. ``force`` records the arrangement but runs every step.

diff-src-parallel (downloads tiers): steps carrying a ``src_host``
(host group of their query destination) execute in WAVES — consecutive
steps with distinct hosts are submitted together and run concurrently;
the whole wave is polled to its exits before the next wave opens, so
step order is never violated and same-host steps never overlap (their
anti-bot pacing stays serial). A step WITHOUT ``src_host`` runs alone
(fully serial — the escape hatch for ordering-dependent or
must-stay-serial steps). The registry dedupe, per-service max_parallel
claims and single-instance file locks all still apply per step.

priority give-way: the wave protection above is per FILE — two
concurrently-running files (the nightly chain and a manual run, the
chain and the /non-daily backfill drive) can still contend for the
machine and the query destinations. The runner is the PRIORITY BOARD:
every step carries its service's priority (the service_config tree's
``priority`` — :func:`_common.service_heartbeat.priority_for`: the
downloads domain outranks every non-download regardless of cadence,
and within a domain the more frequent the cadence the higher), and a
step gives way to any STRICTLY higher-priority step of another run —
a lower-priority wave step (and the /non-daily drive's re-run) holds
back before its submit while one is in flight, and a higher-priority
run arriving MID-RUN stops the child (registry stop) until that run
finishes, then resumes it as a fresh child of the same module — the
tasks' incremental guards pick up where the stop left off. Equal
priorities never give way to each other (same-rank runs stay
concurrent, as before). Runs adopted after a monitor restart carry no
step plan, so they claim no board (the chain aborts on a lost run
anyway — the same in-memory boundary).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .data_definitions import (
    OrchestrationInFlightResponse, OrchestrationInFlightRun,
    OrchestrationPlan, OrchestrationRunAccepted, OrchestrationRunRequest,
    OrchestrationRunStatus, OrchestrationStep, OrchestrationStepStatus,
)

# The caution trail (monitor.caution_log, the Monitor UI's Cautions tab):
# a step failure is counted in the tier state (in-memory only) AND logged
# as a caution row so it stays queryable after the server restarts.
from _common.caution_log import record_service_error

# The step's give-way rank — the service_config tree's ``priority``
# (downloads domain +100 over the freq rank; higher = wins conflicts).
from _common.service_heartbeat import priority_for

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATION_DIR = _REPO_ROOT / "scripts" / "orchestration"

# Poll cadence for a step's completion (the registry sweep's cadence is
# 5s; 2s keeps tier runs responsive without spinning).
_STEP_POLL_S = 2.0

# Diff-src-parallel wave width cap: at most this many concurrent child
# processes per wave (RAM / DB-write load bound). Env-overridable.
_WAVE_WIDTH_ENV = "ORCHESTRATION_MAX_WAVE_WIDTH"
_DEFAULT_WAVE_WIDTH = 8

# Cap on waiting out a stopped (give-way) child's exit — SIGTERM grace
# then SIGKILL bounds the child itself; the cap only guards against a
# stalled registry sweep. Generous, and never reached in practice.
_YIELD_EXIT_CAP_S = 300.0

# Child exit-code contracts (frozen; canonical definitions in
# _common.service_heartbeat.EXIT_ALREADY_RUNNING and
# _common.pre_check.EXIT_RESOURCE — mirrored here to keep pandas out of
# this module's import graph):
#   EXIT_ALREADY_RUNNING — the child refused itself via the heartbeat's
#     max_parallel / parallel-services guard: the work IS proceeding in
#     the surviving run (a re-started tier racing its own detached
#     children, 2026-11-20 downloads_quarterly) — count the step OK.
#   EXIT_RESOURCE — the child's resource pre-check failed (GPU VRAM /
#     RAM starved by a finishing sibling): defer to ONE end-of-tier
#     retry pass; the missing resource frees asynchronously.
_EXIT_ALREADY_RUNNING = 10
_EXIT_RESOURCE = 11


def _max_wave_width() -> int:
    raw = os.environ.get(_WAVE_WIDTH_ENV)
    if raw is None:
        return _DEFAULT_WAVE_WIDTH
    try:
        width = int(raw)
    except ValueError:
        logger.warning("%s=%r unreadable — using %d",
                       _WAVE_WIDTH_ENV, raw, _DEFAULT_WAVE_WIDTH)
        return _DEFAULT_WAVE_WIDTH
    return width if width >= 1 else _DEFAULT_WAVE_WIDTH


class OrchestrationRunner:
    """One in-flight tier run per tag (thread-safe; the router resolves
    the instance off app.state)."""

    def __init__(self, registry) -> None:
        self._registry = registry
        self._lock = threading.Lock()
        self._runs: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # plan
    # ------------------------------------------------------------------

    @staticmethod
    def plan(file: str) -> OrchestrationPlan:
        """One orchestration file's steps (403-safe name: no separators,
        must exist)."""
        if not file or "/" in file or "\\" in file or ".." in file:
            raise HTTPException(
                status_code=422, detail=f"orchestration file {file!r} "
                "must be a bare name under scripts/orchestration/")
        path = ORCHESTRATION_DIR / f"{file}.json"
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail=f"no orchestration file at {path}")
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"orchestration file {path} unreadable: {exc}") from exc
        steps = []
        for num in sorted(doc, key=lambda k: int(k)):
            step = doc[num]
            steps.append(OrchestrationStep(
                num=str(num), script=step["script"],
                args=[str(a) for a in step.get("args", [])],
                src_host=step.get("src_host"),
                can_be_parallel=bool(step.get("can_be_parallel")),
                note=step.get("note"),
                priority=priority_for(str(step["script"]))))
        return OrchestrationPlan(file=file, steps=steps)

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def start(self, file: str,
              skip_services: set[str] | None = None) -> OrchestrationRunAccepted:
        """Begin one tier run (a second run of the SAME file while one
        is in flight is refused — the steps' own max_parallel claims
        backstop the module-level dedupe). ``skip_services`` is the
        ARRANGEMENT's not-due set (the maintainer-driven run path): the
        listed steps are pre-marked ``skipped_not_due`` and never spawned
        — the run records them without counting them as failures."""
        plan = self.plan(file)
        skip = skip_services or set()
        tag = f"orchestration:{file}:{int(time.time())}"
        with self._lock:
            running = [r for r in self._runs.values()
                       if r["file"] == file and r["state"] == "running"]
            if running:
                raise HTTPException(
                    status_code=409,
                    detail=f"orchestration file {file!r} is already "
                           f"running under tag {running[0]['tag']}")
            self._runs[tag] = {
                "tag": tag, "file": file, "state": "running",
                "steps": {
                    s.num: {"num": s.num, "script": s.script,
                            "src_host": s.src_host,
                            "priority": s.priority,
                            "state": ("skipped_not_due" if s.script in skip
                                      else "pending"),
                            "tag": None, "exit_code": None}
                    for s in plan.steps},
            }
        threading.Thread(
            target=self._run_steps, name=f"orchestration-{file}",
            args=(tag, plan, skip), daemon=True).start()
        return OrchestrationRunAccepted(
            tag=tag, file=file, steps=plan.steps)

    @staticmethod
    def _build_waves(steps: list[OrchestrationStep]) -> list[list]:
        """Partition the tier's steps into consecutive execution waves.

        Greedy fill in step order: a step joins the open wave iff the
        wave was opened by a ``src_host`` step, the step itself carries
        a ``src_host``, that host is not yet claimed in the wave, and
        the wave is under the width cap. Anything else (no ``src_host``,
        a same-host repeat, or a full wave) closes the wave and opens
        the next with this step — a no-``src_host`` step thus ALWAYS
        runs alone (its serial escape hatch), and same-host steps never
        share a wave. Waves run one at a time, so step order between
        waves is fully preserved.
        """
        cap = _max_wave_width()
        waves: list[list[OrchestrationStep]] = []
        current: list[OrchestrationStep] = []
        hosts: set[str] = set()
        for step in steps:
            joinable = (step.src_host is not None and current
                        and current[0].src_host is not None
                        and step.src_host not in hosts
                        and len(current) < cap)
            if not joinable:
                if current:
                    waves.append(current)
                current = [step]
                hosts = {step.src_host} if step.src_host else set()
            else:
                current.append(step)
                hosts.add(step.src_host or "")
        if current:
            waves.append(current)
        return waves

    def _run_steps(self, tag: str, plan: OrchestrationPlan,
                   skip: set[str]) -> None:
        """The runner thread: execute the tier's waves one at a time,
        each wave's steps submitted together and polled to their exits
        (failures counted, never fatal). Steps whose resource pre-check
        failed (exit _EXIT_RESOURCE) get ONE retry pass after the tier
        body — a sibling step finishing (e.g. the GPU-heavy builds tier
        before analysis_daily) frees the resource asynchronously.
        Arrangement-skipped steps (``skip``, state skipped_not_due from
        the start) never enter the waves."""
        runnable = [s for s in plan.steps if s.script not in skip]
        if skip:
            logger.info("orchestration %s: %d step(s) skipped by the "
                        "arrangement (not due): %s", plan.file, len(skip),
                        " ".join(sorted(skip)))
        waves = self._build_waves(runnable)
        if len(waves) != len(runnable):
            logger.info("orchestration %s: %d step(s) -> %d wave(s) "
                        "(diff-src-parallel)", plan.file, len(runnable),
                        len(waves))
        failed = 0
        deferred: list[OrchestrationStep] = []
        for idx, wave in enumerate(waves, start=1):
            logger.info("orchestration %s: wave %d/%d: %s", plan.file, idx,
                        len(waves), " ".join(
                            f"{s.num}[{s.src_host or 'serial'}]"
                            for s in wave))
            n_failed, wave_deferred = self._run_wave(tag, plan.file, wave)
            failed += n_failed
            deferred.extend(wave_deferred)
        if deferred:
            logger.info("orchestration %s: retrying %d resource-starved "
                        "step(s) after the tier body: %s", plan.file,
                        len(deferred), " ".join(s.script for s in deferred))
            n_failed, _ = self._run_wave(tag, plan.file, deferred,
                                         resource_retry=True)
            failed += n_failed
        with self._lock:
            self._runs[tag]["state"] = "failed" if failed else "done"
        logger.info("orchestration %s: %s (%d step(s) failed)",
                    plan.file, "failed" if failed else "ok", failed)

    def _run_wave(self, tag: str, file: str,
                  wave: list[OrchestrationStep],
                  resource_retry: bool = False,
                  ) -> tuple[int, list[OrchestrationStep]]:
        """One wave: submit every step, poll the set of in-flight runs
        to their exits (the serial loop generalized to a set). A step
        whose priority is STRICTLY lower than a running step of another
        run gives way (the service_config priority board — downloads
        outrank non-downloads, then frequency): it holds back before
        its submit while a higher-priority run is in flight, and a
        higher-priority run arriving mid-run STOPS the child (registry
        stop) until it finishes, then resumes it as a fresh child of
        the same module — the task's incremental guards pick up where
        the stop left off.

        An exit-_EXIT_ALREADY_RUNNING child counts OK (the work runs in
        the surviving instance); an exit-_EXIT_RESOURCE child is
        DEFERRED (returned to the caller for the end-of-tier retry
        pass) unless ``resource_retry`` — the retry pass treats every
        non-zero, non-dedupe exit as a plain failure. Returns
        (failed-step count, deferred steps)."""
        failed = 0
        deferred: list[OrchestrationStep] = []
        pending: dict[str, OrchestrationStep] = {}
        held: list[OrchestrationStep] = []
        yields: dict[str, int] = {}
        for step in wave:
            blocker = self._blocking_claimant(step.priority, tag)
            if blocker is not None:
                self._hold_step(tag, file, step.num,
                                f"higher-priority run in flight "
                                f"({blocker})")
                held.append(step)
                continue
            failed += self._submit_step(tag, file, step, pending)
        while pending or held:
            for step in list(held):
                blocker = self._blocking_claimant(step.priority, tag)
                if blocker is not None:
                    continue
                held.remove(step)
                logger.info("orchestration %s step %s (%s): priority "
                            "board clear — resuming", file, step.num,
                            step.script)
                failed += self._submit_step(tag, file, step, pending)
            statuses = (self._registry.status_many(list(pending))
                        if pending else {})
            for entry_tag, step in list(pending.items()):
                status = statuses.get(entry_tag)
                if status is not None and status.get("running", True):
                    blocker = self._blocking_claimant(step.priority, tag)
                    if blocker is None:
                        continue
                    yields[step.num] = yields.get(step.num, 0) + 1
                    logger.warning(
                        "orchestration %s step %s (%s, P%d): %s — "
                        "stopping to give way (yield %d)", file,
                        step.num, step.script, step.priority, blocker,
                        yields[step.num])
                    self._registry.stop(entry_tag)
                    del pending[entry_tag]
                    if not self._await_run_exit(entry_tag):
                        failed += self._fail_step(
                            tag, file, step, None,
                            f"still running after the give-way stop "
                            f"({blocker}) — resume abandoned")
                    else:
                        self._hold_step(
                            tag, file, step.num,
                            f"yielded to higher-priority run ({blocker})")
                        held.append(step)
                    continue
                code = status.get("exit_code") if status else None
                if code == _EXIT_ALREADY_RUNNING:
                    # The child refused itself (heartbeat max_parallel /
                    # parallel-services guard): the work IS proceeding in
                    # the surviving run — deduped, counted OK, no caution.
                    with self._lock:
                        self._runs[tag]["steps"][step.num].update(
                            state="done", exit_code=code)
                    logger.info("orchestration %s step %s (%s): already "
                                "running under another run (auto-exit %d) "
                                "— deduped, counted OK",
                                file, step.num, step.script, code)
                    del pending[entry_tag]
                    continue
                if code == _EXIT_RESOURCE and not resource_retry:
                    # Resource pre-check fail (GPU VRAM / RAM held by a
                    # finishing sibling): DEFER to the tier's one retry
                    # pass — the resource frees asynchronously. Tentative
                    # failed state until the retry resolves it.
                    deferred.append(step)
                    with self._lock:
                        self._runs[tag]["steps"][step.num].update(
                            state="failed", exit_code=code)
                    logger.warning("orchestration %s step %s (%s): resource "
                                   "pre-check failed (exit %d) — deferred "
                                   "to the end-of-tier retry",
                                   file, step.num, step.script, code)
                    del pending[entry_tag]
                    continue
                with self._lock:
                    self._runs[tag]["steps"][step.num].update(
                        state="done" if (code == 0) else "failed",
                        exit_code=code)
                if code != 0:
                    failed += 1
                    logger.warning("orchestration %s step %s (%s) FAILED "
                                   "(exit %s)", file, step.num,
                                   step.script, code)
                    record_service_error(
                        f"orchestration {file} step {step.num} "
                        f"({step.script}) FAILED (exit {code})",
                        dedupe_key=f"exit:{step.script}",
                        detail={"tier": file, "step": step.num,
                                "script": step.script, "exit_code": code},
                        service=step.script,
                    )
                del pending[entry_tag]
            if pending or held:
                time.sleep(_STEP_POLL_S)
        return failed, deferred

    # ------------------------------------------------------------------
    # priority give-way (the runner is the priority board)
    # ------------------------------------------------------------------

    def priority_claims(self, exclude_tag: str | None = None
                        ) -> dict[int, list[str]]:
        """priority -> claimant labels (``file#num script``) over the
        RUNNING steps of in-flight runs — the give-way board this
        runner's lower-ranked waves (and the /non-daily backfill drive)
        poll. The caller's own run is excluded via ``exclude_tag``:
        same-run steps never block each other (a run's waves already
        order its own steps)."""
        with self._lock:
            claims: dict[int, list[str]] = {}
            for run in self._runs.values():
                if run["state"] != "running" or run["tag"] == exclude_tag:
                    continue
                for record in run["steps"].values():
                    if record["state"] == "running":
                        claims.setdefault(record["priority"], []).append(
                            f"{run['file']}#{record['num']} "
                            f"{record['script']}")
            return claims

    def _blocking_claimant(self, priority: int, exclude_tag: str
                           ) -> str | None:
        """The highest claimant STRICTLY above ``priority`` on the
        board, if any (None = clear to run). Equal priorities never
        block — same-rank runs stay concurrent, as before."""
        claims = self.priority_claims(exclude_tag)
        top = max((p for p in claims if p > priority), default=None)
        if top is None:
            return None
        return f"{claims[top][0]} [P{top}]"

    def _submit_step(self, tag: str, file: str, step: OrchestrationStep,
                     pending: dict[str, OrchestrationStep]) -> int:
        """Submit one wave step through the registry (the dedupe still
        applies — two tiers racing one module converge on the same
        entry). Returns the counted-failure delta (1 on a submit
        failure, else 0)."""
        with self._lock:
            record = self._runs[tag]["steps"][step.num]
            record["state"] = "running"
        step_tag = f"{tag}:{step.num}:{step.script}"
        try:
            entry, already = self._registry.submit(
                step.script, step.args, step_tag)
        except Exception as exc:  # noqa: BLE001 — a submit failure is
            logger.exception(  # counted, never fatal
                "orchestration step submit failed")
            with self._lock:
                record.update(state="failed", exit_code=None,
                              tag=step_tag)
            record_service_error(
                f"orchestration {file} step {step.num} submit failed "
                f"({step.script}): {exc}",
                dedupe_key=f"submit:{step.script}",
                detail={"tier": file, "step": step.num,
                        "script": step.script, "args": step.args,
                        "error": str(exc), "tag": step_tag},
                service=step.script,
            )
            return 1
        with self._lock:
            record["tag"] = entry.tag
        if already:
            # Same tag in flight — the registry deduped; poll the
            # SAME entry (two tiers racing one module converge).
            logger.info("orchestration %s step %s (%s) deduped to an "
                        "in-flight run", file, step.num, step.script)
        pending[entry.tag] = step
        return 0

    def _hold_step(self, tag: str, file: str, num: str,
                   reason: str) -> None:
        """Park one step in the ``waiting_priority`` state until the
        higher-priority run clears (the wave loop resumes it)."""
        with self._lock:
            self._runs[tag]["steps"][num]["state"] = "waiting_priority"
        logger.info("orchestration %s step %s waiting_priority: %s",
                    file, num, reason)

    def _fail_step(self, tag: str, file: str, step: OrchestrationStep,
                   code: int | None, reason: str) -> int:
        """Mark one step failed with a detail line (the defensive
        give-way branch); returns the counted-failure delta."""
        with self._lock:
            self._runs[tag]["steps"][step.num].update(
                state="failed", exit_code=code)
        logger.error("orchestration %s step %s (%s): %s",
                     file, step.num, step.script, reason)
        record_service_error(
            f"orchestration {file} step {step.num} ({step.script}) "
            f"FAILED: {reason}",
            dedupe_key=f"exit:{step.script}",
            detail={"tier": file, "step": step.num,
                    "script": step.script, "exit_code": code,
                    "reason": reason},
            service=step.script,
        )
        return 1

    def _await_run_exit(self, entry_tag: str) -> bool:
        """Poll one registry tag to its exit (a give-way-stopped child:
        SIGTERM grace then SIGKILL bounds the child; the cap only
        guards a stalled registry sweep). False when still running at
        the cap."""
        deadline = time.monotonic() + _YIELD_EXIT_CAP_S
        while time.monotonic() < deadline:
            status = self._registry.status_many([entry_tag]).get(entry_tag)
            if status is None or not status.get("running", True):
                return True
            time.sleep(_STEP_POLL_S)
        return False

    def status(self, tag: str) -> OrchestrationRunStatus:
        with self._lock:
            run = self._runs.get(tag)
            if run is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"no orchestration run under tag {tag!r}")
            steps = [OrchestrationStepStatus(**record)
                     for record in run["steps"].values()]
            return OrchestrationRunStatus(
                tag=run["tag"], file=run["file"], state=run["state"],
                steps=steps)

    def in_flight(self) -> OrchestrationInFlightResponse:
        """Every currently-running tier run — the maintainer's nightly
        chain adopts a same-file run instead of racing it (the old
        per-tier flock await)."""
        with self._lock:
            return OrchestrationInFlightResponse(runs=[
                OrchestrationInFlightRun(tag=r["tag"], file=r["file"])
                for r in self._runs.values() if r["state"] == "running"])


router = APIRouter(tags=["orchestration"])


@router.get("/orchestration", response_model=list[str])
async def list_orchestration() -> list[str]:
    """The orchestration file names (bare, .json stripped)."""
    return sorted(p.stem for p in ORCHESTRATION_DIR.glob("*.json"))


# Declared before /orchestration/{file} — the path-param route would
# otherwise capture "in-flight" as a file name.
@router.get("/orchestration/in-flight",
            response_model=OrchestrationInFlightResponse)
async def orchestration_in_flight(
        request: Request) -> OrchestrationInFlightResponse:
    return request.app.state.orchestration.in_flight()


@router.get("/orchestration/{file}", response_model=OrchestrationPlan)
async def get_orchestration(file: str) -> OrchestrationPlan:
    return OrchestrationRunner.plan(file)


# Sync ``def`` on purpose — the arrange side runs the arranger's
# asyncio.run faces, which need the threadpool (an async def would call
# asyncio.run from the event loop's own thread and raise).
@router.post("/orchestration/run", response_model=OrchestrationRunAccepted)
def run_orchestration(request: Request, req: OrchestrationRunRequest
                      ) -> OrchestrationRunAccepted:
    """Start one tier run. ``arrange`` (the maintainer's nightly chain)
    first upserts the tier's arrangement verdicts
    (monitor.orchestration_task_state) and skips the not-due steps;
    ``force`` records the arrangement but runs every step anyway (the
    manual re-run escape hatch)."""
    skip: set[str] = set()
    if req.arrange:
        arranged = request.app.state.arrangement.arrange([req.file])
        if not req.force:
            skip = set(arranged.not_due_services)
    return request.app.state.orchestration.start(req.file,
                                                 skip_services=skip)


@router.get("/orchestration/run/{tag}",
            response_model=OrchestrationRunStatus)
async def orchestration_status(request: Request,
                               tag: str) -> OrchestrationRunStatus:
    return request.app.state.orchestration.status(tag)
