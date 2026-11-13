"""test.dummy_llm — the offline DUMMY LLM double: on matched what
request, return what response (the 2026-11-12 split — the request→
response engine migrated here out of ``test.dummy_langgraph``, which
keeps only the kg/jev cucumber assertion datasets).

* :class:`DummyLlmProvider` (historical name
  :class:`DummyLanggraphProvider`) — one ``chat_complete`` per the
  harness's provider contract, matching the request against the GHERKIN
  FEATURE predicates under ``features/`` and returning the Then
  DocString; ``estimate_usage`` is the shared script-aware formula (2
  CJK chars / token, 2 tokens / English word) the HTTP envelope and
  the in-process audit both report.
* ``_gherkin`` — the strict minimal Gherkin parser behind the dataset
  (re-exported; the sibling ``test.dummy_langgraph`` package imports it
  back from here).
"""
from test.dummy_llm._gherkin import (
    FeatureSpec, GherkinParseError, ScenarioSpec, StepSpec,
    load_features, parse_feature,
)
from test.dummy_llm.provider import (
    AUDIT_URL, FIXED_COMPLETION_TOKENS, FIXED_PROMPT_TOKENS,
    DummyLanggraphProvider, DummyLlmProvider, ReplyNotFound,
    estimate_usage,
)

__all__ = [
    "DummyLlmProvider", "DummyLanggraphProvider", "ReplyNotFound",
    "estimate_usage", "AUDIT_URL",
    "FIXED_PROMPT_TOKENS", "FIXED_COMPLETION_TOKENS",
    "FeatureSpec", "ScenarioSpec", "StepSpec", "GherkinParseError",
    "parse_feature", "load_features",
]
