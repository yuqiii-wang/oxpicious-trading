"""test.dummy_langgraph.assertions — the SHARED full-chain assertion
battery: the checks every dummy langgraph walk must pass, extracted
from ``temp_scripts/probe_dummy_langgraph.py::full_chain`` (2026-11-14)
so the standing probe AND the daemon's ``[this is test]`` self-test run
(``llm_agents/deep_agents/selftest.py`` — a run whose goal starts with
the prefix loads the dummy LLM and auto-triggers THIS battery) assert
through ONE source of truth.

* :data:`SUMMARY_CHAIN_GOAL` — the canonical dataset goal (the walk's
  default when a self-test input carries no goal text after the
  prefix).
* :class:`RecordingDecisionEngine` — a REAL System One engine with a
  read ledger (moved out of the probe): every ``decide_*`` read is
  recorded while the real model computes, so the battery can assert
  the jev lanes actually read.
* :class:`SummaryChainAssert` — the battery over one settled
  ``run_thread`` outcome: the walked scenario tags, the deliberate
  flake + supervisor resume, the db turns through the seam, the jev
  reads, the three-thread chain and the four completed review seats.
* :class:`SelfTestReport` / :class:`AssertCheck` — the typed report the
  battery returns (:meth:`SelfTestReport.render` is the run-answer
  face the daemon's self-test returns).
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from llm_agents.deep_agents.langgraph.decision.engine import JevDecisionEngine
from llm_agents.deep_agents.langgraph.runner import ThreadRunResult
from llm_agents.deep_agents.planning.plan_json import REVIEW_NODE_TAGS
from llm_agents.deep_agents.planning.store import StateRow
from test.dummy_llm.provider import DummyLlmProvider
from test.dummy_langgraph.mcp import FeatureToolSeam

# The canonical base-walk goal — the dataset's needles ride it (the
# walk itself is goal-agnostic past the planner, but the DEFAULT when a
# self-test input names no goal of its own is the text the dataset was
# built around; from probe_dummy_langgraph.GOAL).
SUMMARY_CHAIN_GOAL = (
    "研究沪深300的均线趋势与量价关系，给出当前市场的综合结论与主要风险")

# The scenario tags the base walk must serve, per graph segment.
_REQUIRED_TAGS: Tuple[str, ...] = (
    "step-trend-kg", "step-volume-kg", "output-custom", "output-final",
    "review-return-estimate", "review-risk-analysis",
    "review-optimistic", "review-pessimistic",
)
# Each business step's db turn: whichever face the jev relevance gate
# produced (the plain db scenario or the gate-rejected synonym nudge).
_DB_TURN_FACES: Tuple[Tuple[str, str], ...] = (
    ("step-trend-db", "step-trend-db-gate"),
    ("step-volume-db", "step-volume-db-gate"),
)


class RecordingDecisionEngine:
    """The REAL System One engine with a read ledger — the jev lane
    computes FOR REAL from the walk's own inputs (the kg gate judges
    every kg_search result, the repair fast path reads the flake
    failure); this wrapper only records each read."""

    def __init__(self, engine: Optional[JevDecisionEngine] = None) -> None:
        self._engine = engine if engine is not None else JevDecisionEngine()
        self.reads: List[Tuple[str, str, Any, bool]] = []

    async def decide_resume(self, **kw: Any):
        d = await self._engine.decide_resume(**kw)
        self.reads.append(("resume", d.action, d.confidence, d.ok))
        return d

    async def decide_spawn(self, **kw: Any):
        d = await self._engine.decide_spawn(**kw)
        self.reads.append(("spawn", d.action, d.confidence, d.ok))
        return d

    async def decide_kg_relevance(self, **kw: Any):
        d = await self._engine.decide_kg_relevance(**kw)
        self.reads.append(("kg_relevance", d.action, d.confidence, d.ok))
        return d


class AssertCheck(BaseModel):
    """One battery check's outcome — ``skipped`` marks a check that
    could not run (e.g. the jev reads with no engine mounted): it never
    fails the report, it just does not count."""

    name: str
    ok: bool
    detail: str = ""
    skipped: bool = False


class SelfTestReport(BaseModel):
    """The battery's typed result; ``render()`` is the run-answer face
    (the daemon's self-test run returns it as the answer, the probe
    prints it)."""

    checks: List[AssertCheck] = Field(default_factory=list)
    answer_head: str = ""
    notes: List[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks if not c.skipped)

    @property
    def n_ok(self) -> int:
        return sum(1 for c in self.checks if not c.skipped and c.ok)

    @property
    def n_failed(self) -> int:
        return sum(1 for c in self.checks if not c.skipped and not c.ok)

    @property
    def n_skipped(self) -> int:
        return sum(1 for c in self.checks if c.skipped)

    def render(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        lines = [
            f"[this is test] {verdict} — {self.n_ok} checks ok"
            + (f", {self.n_failed} FAILED" if self.n_failed else "")
            + (f", {self.n_skipped} skipped" if self.n_skipped else ""),
        ]
        for c in self.checks:
            mark = "--" if c.skipped else ("ok" if c.ok else "FAIL")
            head = f"  {mark:>4} — {c.name}"
            lines.append(head + (f": {c.detail}" if c.detail else ""))
        if self.answer_head:
            lines.append(f"walk answer (head): {self.answer_head}")
        lines.extend(f"note: {n}" for n in self.notes)
        return "\n".join(lines)


class SummaryChainAssert:
    """The full-chain battery over one settled dummy walk — the checks
    ``probe_dummy_langgraph.full_chain`` has always made, as ONE object
    both the probe and the daemon's ``[this is test]`` self-test run
    call. ``jev_reads=None`` (no engine mounted, e.g. a
    ``JEV_DECISIONS=0`` daemon) degrades the two jev-read checks to
    SKIPPED — never a failure. ``plan_tag`` names the plan scenario the
    walk's planner must have matched (the canonical ``plan-proposal``
    vs the showcase chain's ``plan-selftest-showcase``); ``extra`` ride
    the report verbatim before the return (the showcase chain's
    spawn/fork/sub-node checks)."""

    def __init__(self, outcome: ThreadRunResult, provider: DummyLlmProvider,
                 seam: FeatureToolSeam,
                 jev_reads: Optional[Sequence[Tuple[str, str, Any, bool]]],
                 plan_tag: str = "plan-proposal",
                 expected_flakes: int = 1,
                 extra: Optional[Sequence[AssertCheck]] = None) -> None:
        self._outcome = outcome
        self._provider = provider
        self._seam = seam
        self._jev_reads = list(jev_reads) if jev_reads is not None else None
        self._plan_tag = plan_tag
        self._expected_flakes = expected_flakes
        self._extra = list(extra) if extra is not None else []

    def run(self) -> SelfTestReport:
        checks: List[AssertCheck] = []
        notes: List[str] = []
        result = self._outcome.result
        used = self._provider.scenarios_used()
        rows: List[StateRow] = list(self._outcome.plan)
        threads = sorted({row.thread_id for row in rows})
        reviews = [row for row in rows if row.node in REVIEW_NODE_TAGS]
        volume_rows = [row for row in rows if "量价" in (row.title or "")]
        db_calls = [c for c in self._seam.calls if c["name"] == "db_run_query"]
        statuses = [row.status for row in rows]

        def check(name: str, ok: bool, detail: str = "") -> None:
            checks.append(AssertCheck(name=name, ok=ok, detail=detail))

        check("the final answer is the result output's",
              "DUMMY FINAL ANSWER" in result.answer)
        check("the planner matched the plan feature",
              self._plan_tag in used)
        check("the expected deliberate flakes were served",
              self._provider.n_flakes == self._expected_flakes,
              f"got {self._provider.n_flakes}")
        check("the supervisor's verdict ask was served the retry feature",
              "supervisor-verdict-retry" in used)
        check("the flaked volume step completed after the resume",
              any(row.status == "completed" for row in volume_rows),
              f"rows: {[(r.node, r.status) for r in volume_rows]}")
        for tag in _REQUIRED_TAGS:
            check(f"scenario {tag} walked", tag in used)
        for step, faces in zip(("trend", "volume"), _DB_TURN_FACES):
            check(f"the {step} step reached its db turn",
                  any(f in used for f in faces))
        check("two db_run_query calls served through the seam",
              len(db_calls) >= 2, f"got {len(db_calls)}")
        if self._jev_reads is None:
            checks.append(AssertCheck(
                name="the jev KG relevance gate read (real computation)",
                ok=True, skipped=True, detail="no engine mounted"))
            checks.append(AssertCheck(
                name="the jev repair fast path read the flake failure",
                ok=True, skipped=True, detail="no engine mounted"))
        else:
            kg_reads = [r for r in self._jev_reads if r[0] == "kg_relevance"]
            resume_reads = [r for r in self._jev_reads if r[0] == "resume"]
            check("the jev KG relevance gate read (real computation)",
                  bool(kg_reads), f"{len(kg_reads)} read(s)")
            check("the jev repair fast path read the flake failure",
                  bool(resume_reads), f"{len(resume_reads)} read(s)")
            if self._jev_reads and not any(r[3] for r in self._jev_reads):
                notes.append("every jev read landed not-ok (resident "
                             "local-model host unreachable?); the lanes "
                             "still exercised")
        check("the chain is THREE threads", len(threads) == 3,
              f"got {len(threads)}")
        check("FOUR review seats ran", len(reviews) == 4,
              f"got {len(reviews)}")
        check("every review seat completed",
              all(r.status == "completed" for r in reviews),
              f"{[(r.node, r.status) for r in reviews]}")
        check("no unsettled rows remain",
              not any(s in ("in_progress", "pending") for s in statuses),
              f"statuses: {statuses}")
        checks.extend(self._extra)
        return SelfTestReport(
            checks=checks, answer_head=result.answer[:200], notes=notes)
