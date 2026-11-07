"""SearchResult — data definition.

Migrated verbatim from :mod:`llm_agents.kg.graph.values` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class SearchResult:
    """A packed subgraph: ranked nodes + the edges that matter — those
    fully inside the chosen set first, then the chosen nodes' top
    edges pointing outward (the join/derivation partners the query
    itself did not name)."""

    nodes: Tuple[ScoredNode, ...]
    edges: Tuple[KgEdge, ...]
    n_entities: int
    n_edges: int
