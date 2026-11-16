"""llm_agents.deep_agents.core.loop.result — the loop's result payload.

``AgentResult`` is the final answer plus how the agent got there — the
payload crossing from the core loop into the graph layer (the runner's
step lanes, the spawn brokers, the thread-result records). Split out of
the former flat ``core/loop.py`` (2026-11-15) so the model layer stands
alone; the composed ``DeepAgent`` re-exports it.
"""
from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field

from llm_agents.deep_agents.core.delegation import SubagentRun


class AgentResult(BaseModel):
    """The final answer plus how the agent got there (the core loop's
    result payload crossing into the graph layer). ``subagent_runs``
    holds the loop's runtime records (``core.delegation.SubagentRun`` —
    tasks and hooks, not serializable), hence the arbitrary-type
    allowance; everything else is plain."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    answer: str
    steps: int
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    subagent_runs: List[SubagentRun] = Field(default_factory=list)
