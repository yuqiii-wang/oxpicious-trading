"""test.dummy_langgraph — the offline cucumber-test datasets for the
ai-summary langgraph chain's KG and JEV assertions (since the
2026-11-12 split; the request→response ENGINE lives in the parallel
package :mod:`test.dummy_llm` — "on matched what request, return what
response" — and this package keeps only the FEATURE datasets):

* ``features/03_tools.feature`` + :class:`FeatureToolSeam` — the KG/DB
  tool-result fixtures: for the matched ``kg_search`` / ``db_run_query``
  call the seam returns the scenario's Then DocString (the kg entities
  render / the query rows).
* ``features/02_decisions.feature`` +
  :class:`StubJevDecisionEngine` / :class:`DecisionChoiceChecker` — the
  decision dataset: for the SAME typed decision input the feature
  asserts the System One choice output, offline through the stub, live
  through the real jev model.
* :mod:`test.dummy_langgraph.assertions` — the SHARED full-chain
  assertion battery (:class:`SummaryChainAssert`) the standing probe
  AND the daemon's ``[this is test]`` self-test run assert through.
* :mod:`test.dummy_langgraph.router_walks` — the SHARED System One
  router walk runner (:func:`run_router_walk`): the six scripted walks
  (spawn / fork / fork-drift / join / probe / background) the standing
  router probe runs leave-no-trace and the daemon's ``[this is test]``
  self-test walks KEEPING the chains so the report shows the fork /
  spawn / interference scenarios.

The whole historical import surface is re-exported, so every
``test.dummy_langgraph`` import keeps resolving.
"""
from test.dummy_llm import (
    DummyLanggraphProvider, DummyLlmProvider, ReplyNotFound,
    estimate_usage,
)
from test.dummy_llm._gherkin import (
    FeatureSpec, GherkinParseError, ScenarioSpec, StepSpec,
    load_features, parse_feature,
)
from test.dummy_langgraph.provider import FEATURES_DIR
from test.dummy_langgraph.jev import (
    DecisionChoiceChecker, DecisionScenario, StubJevDecisionEngine,
    load_decision_book,
)
from test.dummy_langgraph.mcp import FeatureToolSeam, ToolResultNotFound
from test.dummy_langgraph.assertions import (
    AssertCheck, RecordingDecisionEngine, SUMMARY_CHAIN_GOAL,
    SelfTestReport, SummaryChainAssert,
)
from test.dummy_langgraph.router_walks import (
    ROUTER_WALK_KINDS, RouterWalkResult, ScriptedRouterEngine,
    ScriptedSandbox, YieldingProvider, run_router_walk,
)

__all__ = [
    "FEATURES_DIR",
    "DummyLanggraphProvider", "DummyLlmProvider", "ReplyNotFound",
    "estimate_usage",
    "StubJevDecisionEngine", "DecisionChoiceChecker", "DecisionScenario",
    "load_decision_book", "FeatureToolSeam", "ToolResultNotFound",
    "FeatureSpec", "ScenarioSpec", "StepSpec", "GherkinParseError",
    "parse_feature", "load_features",
    "AssertCheck", "RecordingDecisionEngine", "SUMMARY_CHAIN_GOAL",
    "SelfTestReport", "SummaryChainAssert",
    "ROUTER_WALK_KINDS", "RouterWalkResult", "ScriptedRouterEngine",
    "ScriptedSandbox", "YieldingProvider", "run_router_walk",
]
