"""GraphOutputSpec — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.langgraph.runner.state` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GraphOutputSpec:
    """One execution graph's FINAL OUTPUT NODE charter: the node's work
    tag, the context KIND its document persists as (the chain handoff —
    'graph-output' feeds the next graph; 'final-result' is the run's
    answer), and the synthesis conversation's fixed directive."""

    node: str
    context_kind: str            # 'graph-output' | 'final-result'
    directive: str
