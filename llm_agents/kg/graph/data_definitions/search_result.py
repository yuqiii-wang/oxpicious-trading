"""SearchResult — data definition.

Migrated verbatim from :mod:`llm_agents.kg.graph.values` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class SearchResult:
    """A packed subgraph: ranked nodes + the edges that matter — those
    fully inside the chosen set first, then the chosen nodes' top
    edges pointing outward (the join/derivation partners the query
    itself did not name). ``trace`` — the rung counters the engine
    filled for this traversal (what the seed / walk rungs produced and
    what the relevance floors pruned); a result built elsewhere (the
    service's gate-filtered copy) carries none."""

    nodes: Tuple[ScoredNode, ...]
    edges: Tuple[KgEdge, ...]
    n_entities: int
    n_edges: int
    trace: Optional[SearchTrace] = None
