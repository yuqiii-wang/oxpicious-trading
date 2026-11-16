"""llm_agents.deep_agents.selftest — the ``[this is test]`` message
prefix: one run that self-tests the langgraph chain (2026-11-14).

A run whose MESSAGE INPUT (the goal — what the Ask AI UI sends to the
daemon) starts with the literal prefix ``[this is test]`` is a
SELF-TEST run: the daemon (:func:`server._run_one`) then

1. LOADS THE DUMMY LLM — the offline in-process feature double
   (:class:`test.dummy_llm.DummyLlmProvider` via
   ``build_chat_provider("dummy")``), regardless of the active
   secret_config row or an explicit ``provider`` override — zero
   provider spend, deterministic;
2. AUTO-TRIGGERS THE DUMMY LANGGRAPH ASSERT — the walk runs through
   the REAL ``run_thread`` chain (plan → custom → result) over the
   ``FeatureToolSeam`` + a recording System One engine, and the SHARED
   full-chain battery
   (:class:`test.dummy_langgraph.assertions.SummaryChainAssert` — the
   same checks ``temp_scripts/probe_dummy_langgraph.py`` makes) runs
   over the settled outcome. BESIDE the canonical chain the self-test
   runs EVERY shared walk (:meth:`full_report`, the shared
   :func:`test.dummy_langgraph.router_walks.run_router_walk` bodies):
   the System One router walks + the tool-usage walks (spawn / fork /
   fork-drift / fork-sub / join / probe / background / reserve / cut /
   repeat / repair / syntax) The
   run's ANSWER becomes the rendered PASS/FAIL report (visible in the
   UI); the walked chains stay in the thread history so the Thread
   Graph renders them.

The goal text AFTER the prefix stays the run's goal (the dataset's
base walk is goal-agnostic — ``@plan-proposal`` keys on the planner's
system text, not the goal); an empty remainder falls back to the
canonical :data:`SUMMARY_CHAIN_GOAL` — the 沪深300 study the UI's
prefix lane runs (the bridge forwards the bare prefix, so the daemon's
fallback IS the walk goal). The walk is MARKET-WIDE: the caller's code
scope (``data.scopes`` / ``data.scope``, :meth:`market_wide_data`) is
dropped before the run, so no ``llm.deep_agent_identities`` row lands
and the history entry shows under every Summary History subject pick.
A mid-walk exception (dataset drift) settles as a FAIL report naming
the error, never a 500 — the report IS the assert output.

stdlib-light module at import (the ``limits.py`` layering rule): the
double / seam / engine imports are lazy inside the builders.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing-only, never imported
    from llm_agents.deep_agents.langgraph.runner import ThreadRunResult
    from test.dummy_langgraph import FeatureToolSeam, RecordingDecisionEngine
    from test.dummy_langgraph.assertions import SelfTestReport
    from test.dummy_llm import DummyLlmProvider

logger = logging.getLogger(__name__)

# The message-input prefix that turns a run into a self-test run.
TEST_PREFIX = "[this is test]"

# The audit/report face of the dummy model (the resident host's dummy-1
# label — the in-process double only echoes it).
TEST_MODEL_LABEL = "dummy-1"

# The MAIN chain is the SHOWCASE (2026-11-14): one graph the UI opens
# right after the run, holding every scenario kind as nodes — trend +
# volume (the flake → supervisor verdict → resume), a spawn step, and
# a fork step whose fork child spawns its own sub node. The marker
# suffix keys the showcase plan face (the [Run goal] block rides every
# step conversation, so the goal must stay free of the per-step
# discriminators; the marker itself keys ONLY the plan face).
SHOWCASE_GOAL_MARKER = "全场景自检"

# The walks the self-test runs BESIDE the canonical summary chain:
# ALL of them (2026-11-14 "must test all scenarios") — the seven
# ROUTER walks (spawn / fork / fork-drift / fork-sub / join / probe /
# background) AND the five TOOL-USAGE walks (reserve / cut / repeat /
# repair / syntax), fetched lazily as
# test.dummy_langgraph.router_walks.ROUTER_WALK_KINDS inside
# :meth:`full_report` (the layering rule keeps this module
# stdlib-light at import). The walks + the canonical chain together
# must cover EVERY chat-dataset scenario — the report's coverage gate
# (dataset_coverage_check) names any miss.


class DummySelfTestRun:
    """One ``[this is test]``-prefixed run's policy — detected at run
    start (:meth:`detect`), it owns the run's substitute collaborators
    (the dummy provider, the feature tool seam, the recording decision
    engine) and the post-walk assertion. One instance serves ONE run;
    the builders memoize (the provider/seam/engine are built once and
    :meth:`assert_outcome` reads them back)."""

    def __init__(self, original_goal: str, remainder: str) -> None:
        self._original_goal = original_goal
        self._remainder = remainder
        self._provider: Optional["DummyLlmProvider"] = None
        self._seam: Optional["FeatureToolSeam"] = None
        self._engine: Optional["RecordingDecisionEngine"] = None
        # The RAW live engine behind the recorder (the decision
        # battery's live lane) — None when the knobs disabled jev.
        self._live_engine: Optional[Any] = None
        # The router walks' chain ids (full_report's walk chains that
        # STAY in the history) — the response carries them
        # (RunResponse.walk_chain_ids) so a driver (the standing
        # prefix probe) can purge them after its assertion pass.
        self._walk_chain_ids: List[str] = []

    @property
    def walk_chain_ids(self) -> List[str]:
        return list(self._walk_chain_ids)

    # ---------------------------------------------------------- detect
    @classmethod
    def detect(cls, goal: str) -> Optional["DummySelfTestRun"]:
        """The self-test policy when *goal* starts with the prefix
        (case-insensitive, surrounding whitespace ignored), else None —
        the plain run path."""
        stripped = (goal or "").strip()
        if not stripped.lower().startswith(TEST_PREFIX):
            return None
        return cls(original_goal=goal,
                   remainder=stripped[len(TEST_PREFIX):].strip())

    @property
    def effective_goal(self) -> str:
        """The goal the walk runs under: the caller's text after the
        prefix (or the canonical dataset goal when none), ALWAYS
        suffixed with the showcase marker — it keys the showcase plan
        face, so the main chain is the four-scenario showcase graph
        whatever the caller typed."""
        base = self._remainder
        if not base:
            from test.dummy_langgraph import SUMMARY_CHAIN_GOAL

            base = SUMMARY_CHAIN_GOAL
        return f"{base} 【{SHOWCASE_GOAL_MARKER}】"

    @property
    def model_label(self) -> str:
        return TEST_MODEL_LABEL

    # ------------------------------------------------- market-wide walk
    def market_wide_data(
        self, data: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """The walk's data with the caller's CODE SCOPE dropped — the
        self-test is a market-wide walk by definition: without the
        ``scopes`` / ``scope`` keys ``identities_from_scope`` records no
        ``llm.deep_agent_identities`` row, so the history entry shows
        under EVERY Summary History subject pick (the filter's
        no-identity fallback) instead of only the picker's code. The
        input dict is never mutated; non-dict data passes through."""
        if not isinstance(data, dict):
            return data
        return {k: v for k, v in data.items()
                if k not in ("scopes", "scope")}

    # ------------------------------------------------------- builders
    def build_provider(self) -> "DummyLlmProvider":
        """The dummy LLM — the in-process feature double through the
        harness's own label path (never the HTTP-sim face: the
        self-test walk is the offline dataset contract)."""
        if self._provider is None:
            from llm_agents.deep_agents.llm_config import build_chat_provider

            self._provider = build_chat_provider("dummy")
        return self._provider

    def build_mcp(self) -> "FeatureToolSeam":
        """The feature tool seam — the dataset's canned db/kg tool
        results (the real MCP server would answer with rows the
        dataset's next-turn predicates do not expect)."""
        if self._seam is None:
            from test.dummy_langgraph import FeatureToolSeam

            self._seam = FeatureToolSeam()
        return self._seam

    def build_sandbox(self) -> Any:
        """The showcase chain's scripted sandbox — the spawn child's and
        the grandchild's write/run results (the real docker sandbox
        would run nothing the dataset's next turns expect)."""
        from test.dummy_langgraph.router_walks import build_showcase_sandbox

        return build_showcase_sandbox()

    def build_showcase_engine(self) -> Any:
        """The MAIN chain's decision engine — the showcase router
        scripting around the recording real engine (the kg-gate and
        resume reads stay real computations; the fork/spawn routing is
        deterministic)."""
        from test.dummy_langgraph.router_walks import ShowcaseEngine

        return ShowcaseEngine(self.build_decision_engine())

    def build_decision_engine(self) -> Optional["RecordingDecisionEngine"]:
        """The recording System One engine over whatever the daemon's
        knobs allow (``JevDecisionEngine.active()`` — the
        ``JEV_DECISIONS=0`` kill-switch yields None, and the battery
        SKIPS its jev checks instead of failing them). The RAW engine
        is stashed beside the recorder — the decision battery's live
        lane reads through it (the recorder's read ledger is the
        canonical walk's)."""
        if self._engine is None:
            from llm_agents.deep_agents.langgraph.decision.engine import (
                JevDecisionEngine)
            from test.dummy_langgraph import RecordingDecisionEngine

            engine = JevDecisionEngine.active()
            self._live_engine = engine
            if engine is not None:
                self._engine = RecordingDecisionEngine(engine)
        return self._engine

    # ---------------------------------------------------------- assert
    async def assert_outcome(self,
                             outcome: "ThreadRunResult") -> "SelfTestReport":
        """The shared full-chain battery over the settled MAIN walk —
        the same checks the standing probe makes, one source of truth —
        under the showcase plan tag, PLUS the showcase chain's own
        checks (the spawn / fork / sub-node scenario nodes the UI
        shows)."""
        from test.dummy_langgraph import SummaryChainAssert
        from test.dummy_langgraph.router_walks import (
            SHOWCASE_PLAN_TAG, showcase_extra_checks)

        assert self._provider is not None and self._seam is not None, (
            "build_provider/build_mcp run before the walk")
        extra = await showcase_extra_checks(outcome, self._provider)
        report = SummaryChainAssert(
            outcome, self._provider, self._seam,
            self._engine.reads if self._engine is not None else None,
            plan_tag=SHOWCASE_PLAN_TAG, expected_flakes=3, extra=extra,
        ).run()
        report.notes.append(
            f"original input: {self._original_goal[:200]}")
        self._log(report)
        return report

    def failure_report(self, exc: BaseException) -> "SelfTestReport":
        """The FAIL report for a walk that RAISED (a dataset drift) —
        the assert output, never a 500."""
        from test.dummy_langgraph import AssertCheck, SelfTestReport

        report = SelfTestReport(
            checks=[AssertCheck(
                name="the walk completed without raising", ok=False,
                detail=f"{type(exc).__name__}: {exc}")],
            notes=[f"original input: {self._original_goal[:200]}"])
        self._log(report)
        return report

    @staticmethod
    def _log(report: "SelfTestReport") -> None:
        if report.passed:
            logger.info("[this is test] PASS — %d checks ok",
                        report.n_ok)
        else:
            logger.error("[this is test] FAIL — %d/%d checks failed",
                         report.n_failed,
                         report.n_ok + report.n_failed)

    # --------------------------------------------------- router walks
    async def full_report(self,
                          outcome: "ThreadRunResult") -> "SelfTestReport":
        """The canonical battery (:meth:`assert_outcome`) PLUS every
        shared walk (:data:`ROUTER_WALK_KINDS` — the router walks AND
        the tool-usage walks) — the ``[this is test]`` answer shows
        the fork / spawn / supervisor-interference / breaker scenarios
        beside the summary chain, and the report's coverage gate
        asserts every chat-dataset scenario is walked (or a documented
        alternative face). Each walk runs its OWN
        fresh provider/seam/scripted engine/sandbox through the REAL
        ``run_thread`` (the same bodies the standing router probe runs)
        with ``purge=False``: the chain STAYS in the history — Summary
        History lists it, the Thread Graph renders its ``spawn_`` /
        ``fork_`` child rows, the abandoned-tail stub, the failed
        child and the investigator — and its chain id rides
        :attr:`walk_chain_ids` for the caller to purge when it wants
        leave-no-trace (the standing prefix probe does). A walk that
        RAISES lands as its own FAIL check — the report IS the assert
        output, never a 500."""
        report = await self.assert_outcome(outcome)
        from test.dummy_langgraph.router_walks import (
            ROUTER_WALK_KINDS, dataset_coverage_check, decision_checks,
            run_router_walk)

        walked = set(self._provider.scenarios_used())
        for kind in ROUTER_WALK_KINDS:
            try:
                walk = await run_router_walk(kind, purge=False)
                self._walk_chain_ids.append(walk.chain_id)
                report.checks.extend(walk.checks)
                walked |= set(walk.provider.scenarios_used())
                report.notes.append(
                    f"router walk {kind}: chain {walk.chain_id} stays in "
                    f"the history (Summary History + Thread Graph)")
            except Exception as exc:  # noqa: BLE001 — the walk's FAIL lane
                from test.dummy_langgraph import AssertCheck

                report.checks.append(AssertCheck(
                    name=f"{kind} walk completed without raising",
                    ok=False, detail=f"{type(exc).__name__}: {exc}"))
        # THE JEV DECISION BATTERY (2026-11-14 f): every decision
        # scenario — the fork jev decisions (route-fork / fork-point),
        # the resume decisions, the spawn-streak / route-spawn /
        # route-join / failure-probe reads — stub-checked (always) and
        # matched against the resident onejev model's argmax (the live
        # lane skips when no engine / host).
        report.checks.extend(await decision_checks(self._live_engine))
        # MUST TEST ALL SCENARIOS: the coverage gate over the union of
        # every run's walked scenarios — a chat-dataset face neither
        # walked nor documented as an alternative FAILS the report.
        report.checks.append(dataset_coverage_check(walked))
        self._log(report)
        return report
