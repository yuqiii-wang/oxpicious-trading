"""llm_agents.kg.graph.values — the graph VALUE TYPES (frozen
dataclasses): one entity node, one hyperedge, and the result carriers
of the rungs — the lexical seeds (SeedHit), the propagation trace
(WalkHit), the blended rank line (ScoredNode), the packed subgraph
(SearchResult)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from llm_agents.kg.graph.data_definitions import (
    KgEdge,
    KgEntity,
    ScoredNode,
    SearchResult,
    SeedHit,
    WalkHit,
)
