"""KgEntity — data definition.

Migrated verbatim from :mod:`llm_agents.kg.graph.values` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class KgEntity:
    """One entity node — in namespace 0, ONE TABLE/VIEW."""

    entity_id: int
    name: str            # schema-qualified, e.g. stats.index_basic_stats
    entity_type: str     # open vocabulary: 'table' | 'view' | …
    description: str
    tags: Tuple[str, ...]

    @property
    def bare_name(self) -> str:
        """The name without its schema prefix (index_basic_stats)."""
        return self.name.rsplit(".", 1)[-1]
