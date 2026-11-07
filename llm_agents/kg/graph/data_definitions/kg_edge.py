"""KgEdge — data definition.

Migrated verbatim from :mod:`llm_agents.kg.graph.values` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class KgEdge:
    """One hyperedge, head-first (member_ids[0] = subject). Cardinality
    2 degenerates to a plain edge; higher cardinalities walk pairwise."""

    edge_id: int
    relation: str
    sub_relation: Optional[str]
    member_ids: Tuple[int, ...]
