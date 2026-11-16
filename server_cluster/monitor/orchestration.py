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

diff-src-parallel (downloads tiers): steps carrying a ``src_host``
(host group of their query destination) execute in WAVES — consecutive
steps with distinct hosts are submitted together and run concurrently;
the whole wave is polled to its exits before the next wave opens, so
step order is never violated and same-host steps never overlap (their
anti-bot pacing stays serial). A step WITHOUT ``src_host`` runs alone
(fully serial — the escape hatch for ordering-dependent or
must-stay-serial steps). The registry dedupe, per-service max_parallel
claims and single-instance file locks all still apply per step.
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
                note=step.get("note")))
        return OrchestrationPlan(file=file, steps=steps)

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------

    def start(self, file: str) -> OrchestrationRunAccepted:
        """Begin one tier run (a second run of the SAME file while one
        is in flight is refused — the steps' own max_parallel claims
        backstop the module-level dedupe)."""
        plan = self.plan(file)
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
                            "state": "pending", "tag": None,
                            "exit_code": None}
                    for s in plan.steps},
            }
        threading.Thread(
            target=self._run_steps, name=f"orchestration-{file}",
            args=(tag, plan), daemon=True).start()
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

    def _run_steps(self, tag: str, plan: OrchestrationPlan) -> None:
        """The runner thread: execute the tier's waves one at a time,
        each wave's steps submitted together and polled to their exits
        (failures counted, never fatal)."""
        waves = self._build_waves(plan.steps)
        if len(waves) != len(plan.steps):
            logger.info("orchestration %s: %d step(s) -> %d wave(s) "
                        "(diff-src-parallel)", plan.file, len(plan.steps),
                        len(waves))
        failed = 0
        for idx, wave in enumerate(waves, start=1):
            logger.info("orchestration %s: wave %d/%d: %s", plan.file, idx,
                        len(waves), " ".join(
                            f"{s.num}[{s.src_host or 'serial'}]"
                            for s in wave))
            failed += self._run_wave(tag, plan.file, wave)
        with self._lock:
            self._runs[tag]["state"] = "failed" if failed else "done"
        logger.info("orchestration %s: %s (%d step(s) failed)",
                    plan.file, "failed" if failed else "ok", failed)

    def _run_wave(self, tag: str, file: str,
                  wave: list[OrchestrationStep]) -> int:
        """One wave: submit every step, poll the set of in-flight runs
        to their exits (the serial loop generalized to a set). Returns
        the wave's failed-step count."""
        failed = 0
        pending: dict[str, OrchestrationStep] = {}
        for step in wave:
            with self._lock:
                record = self._runs[tag]["steps"][step.num]
                record["state"] = "running"
            step_tag = f"{tag}:{step.num}:{step.script}"
            try:
                entry, already = self._registry.submit(
                    step.script, step.args, step_tag)
            except Exception:  # noqa: BLE001 — a submit failure is
                logger.exception(  # counted, never fatal
                    "orchestration step submit failed")
                with self._lock:
                    record.update(state="failed", exit_code=None,
                                  tag=step_tag)
                failed += 1
                continue
            with self._lock:
                record["tag"] = entry.tag
            if already:
                # Same tag in flight — the registry deduped; poll the
                # SAME entry (two tiers racing one module converge).
                logger.info("orchestration %s step %s (%s) deduped to an "
                            "in-flight run", file, step.num, step.script)
            pending[entry.tag] = step
        while pending:
            statuses = self._registry.status_many(list(pending))
            for entry_tag, step in list(pending.items()):
                status = statuses.get(entry_tag)
                if status is not None and status.get("running", True):
                    continue
                code = status.get("exit_code") if status else None
                with self._lock:
                    self._runs[tag]["steps"][step.num].update(
                        state="done" if (code == 0) else "failed",
                        exit_code=code)
                if code != 0:
                    failed += 1
                    logger.warning("orchestration %s step %s (%s) FAILED "
                                   "(exit %s)", file, step.num,
                                   step.script, code)
                del pending[entry_tag]
            if pending:
                time.sleep(_STEP_POLL_S)
        return failed

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


@router.post("/orchestration/run", response_model=OrchestrationRunAccepted)
async def run_orchestration(request: Request, req: OrchestrationRunRequest
                            ) -> OrchestrationRunAccepted:
    return request.app.state.orchestration.start(req.file)


@router.get("/orchestration/run/{tag}",
            response_model=OrchestrationRunStatus)
async def orchestration_status(request: Request,
                               tag: str) -> OrchestrationRunStatus:
    return request.app.state.orchestration.status(tag)
