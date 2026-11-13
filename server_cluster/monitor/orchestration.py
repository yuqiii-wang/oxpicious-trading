"""server_cluster.monitor.orchestration — the tier runner + router.

The server-side shape of ``scripts/run_steps.sh``: one orchestration
file's steps run one by one, in numeric key order, each submitted
through this area's run registry (the shared jobs surface — so status
polling, log tails, and POST /jobs/{tag}/stop all work for free) and
POLLED to completion before the next step starts. Step failures are
counted, never fatal to the tier (run_steps.sh semantics: monitoring is
alert-only; a failed step just marks the tier failed). The runner's
states live in memory (one server, in-flight tiers only — the durable
run trail is the registry's + each step's own service_audit rows).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .data_definitions import (
    OrchestrationPlan, OrchestrationRunAccepted, OrchestrationRunRequest,
    OrchestrationRunStatus, OrchestrationStep, OrchestrationStepStatus,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATION_DIR = _REPO_ROOT / "scripts" / "orchestration"

# Poll cadence for a step's completion (the registry sweep's cadence is
# 5s; 2s keeps tier runs responsive without spinning).
_STEP_POLL_S = 2.0


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

    def _run_steps(self, tag: str, plan: OrchestrationPlan) -> None:
        """The serial runner thread: submit each step, poll to its
        exit, count failures (never fatal)."""
        failed = 0
        for step in plan.steps:
            with self._lock:
                record = self._runs[tag]["steps"][step.num]
                record["state"] = "running"
            step_tag = f"{tag}:{step.num}:{step.script}"
            try:
                entry, already = self._registry.submit(
                    step.script, step.args, step_tag)
            except Exception as exc:  # noqa: BLE001 — a submit failure is
                logger.exception("orchestration step submit failed")  # noqa: BLE001
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
                            "in-flight run", plan.file, step.num,
                            step.script)
            while True:
                status = self._registry.status_many([entry.tag]).get(
                    entry.tag)
                if status is None or not status.get("running", True):
                    code = status.get("exit_code") if status else None
                    break
                time.sleep(_STEP_POLL_S)
            with self._lock:
                record.update(
                    state="done" if (code == 0) else "failed",
                    exit_code=code)
            if code != 0:
                failed += 1
                logger.warning("orchestration %s step %s (%s) FAILED "
                               "(exit %s)", plan.file, step.num,
                               step.script, code)
        with self._lock:
            self._runs[tag]["state"] = "failed" if failed else "done"
        logger.info("orchestration %s: %s (%d step(s) failed)",
                    plan.file, "failed" if failed else "ok", failed)

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


router = APIRouter(tags=["orchestration"])


@router.get("/orchestration", response_model=list[str])
async def list_orchestration() -> list[str]:
    """The orchestration file names (bare, .json stripped)."""
    return sorted(p.stem for p in ORCHESTRATION_DIR.glob("*.json"))


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
