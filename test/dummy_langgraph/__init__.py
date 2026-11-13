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

__all__ = [
    "FEATURES_DIR",
    "DummyLanggraphProvider", "DummyLlmProvider", "ReplyNotFound",
    "estimate_usage",
    "StubJevDecisionEngine", "DecisionChoiceChecker", "DecisionScenario",
    "load_decision_book", "FeatureToolSeam", "ToolResultNotFound",
    "FeatureSpec", "ScenarioSpec", "StepSpec", "GherkinParseError",
    "parse_feature", "load_features",
]
