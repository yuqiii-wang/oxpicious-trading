"""ThreadRunResult — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.langgraph.runner.state` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
from llm_agents.deep_agents.core.loop import AgentResult
from llm_agents.deep_agents.planning.store import StateRow


@dataclass
class ThreadRunResult:
    """The agent's answer plus the chain it ran under (2026-11-06
    three-graph refactor: one run = plan → custom → result, three
    threads sharing ``super_thread_id`` — the root thread's id).

    ``n_tool_calls`` is the RUN-LEVEL count (accumulated across both
    execution graphs' hops and their forks — the per-conversation
    transcripts ride the run context side channel and are empty on
    reconstruction); ``result.subagent_runs`` is likewise empty on
    reconstruction. ``plan`` is the settled CHAIN row list re-read
    from the plan_store AFTER the graphs complete — it never rode a
    checkpointed state. ``thread_id`` is the chain's ROOT (the plan
    thread — the id the server registered the run under); degraded
    runs carry it too (their whole chain IS the root thread)."""

    result: AgentResult
    thread_id: Optional[str] = None
    super_thread_id: Optional[str] = None
    plan: List[StateRow] = field(default_factory=list)
    n_tool_calls: int = 0
