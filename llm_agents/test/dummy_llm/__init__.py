"""llm_agents.test.dummy_llm — the DUMMY LLM (offline harness tests).

``DummyLlmProvider`` stands in for ``ZhipuChatProvider`` with canned,
deterministic replies — no API key, no network, zero provider spend —
so langgraph and deep-agent tests exercise the REAL harness (core loop,
planner, three-graph chain, review wave, repair lane) offline. The
reply strategy lives in ``DummyReplyBook``: a rule catalog of DIFF
dummy responses keyed by DIFF query texts, shape fallbacks that settle
any conversation, and a ``TOOL:name {json}`` query directive for
driving arbitrary tools. The sibling ``server.py`` serves the same
engine over HTTP (Bearer auth, the completion envelope, HTTP 429
rate-limit errors with zhipu codes) so the REAL provider transport runs
against the simulation. See the module docstrings for the details and
``temp_scripts/test_dummy_llm.py`` for the live contract asserts.
"""
from llm_agents.test.dummy_llm.provider import DummyCall, DummyLlmProvider
from llm_agents.test.dummy_llm.replies import (
    DEFAULT_RULES, DummyReplyBook, PlanStepSpec, ReplyRule, ReplySpec,
    ToolCallSpec,
)
from llm_agents.test.dummy_llm.server import (
    CHAT_PATHS, DEFAULT_PORT, DummyLlmServer, create_app,
    rate_limit_body,
)

__all__ = [
    "CHAT_PATHS", "DEFAULT_PORT", "DEFAULT_RULES", "DummyCall",
    "DummyLlmProvider", "DummyLlmServer", "DummyReplyBook", "PlanStepSpec",
    "ReplyRule", "ReplySpec", "ToolCallSpec", "create_app",
    "rate_limit_body",
]
