"""_RunContext — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.langgraph.runner.state` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from llm_agents.deep_agents.core.loop import AgentResult
from llm_agents.deep_agents.planning.session import PlanSessionResult


@dataclass
class _RunContext:
    """Per-run side channel for the NON-persistable pieces: the ORIGINAL
    execute exception (an exception object is not checkpoint-serializable;
    the state carries its string, the context carries the thing to
    re-raise), the full AgentResult (its tool_calls transcript must
    not ride the state — the state carries n_tool_calls only), and the
    plan session's result (its ThreadPlan/payload are not plain values
    — the plan graph's nodes pass them to the orchestrator through
    here)."""

    error: Optional[BaseException] = None
    result: Optional[AgentResult] = None
    plan_result: Optional[PlanSessionResult] = None
