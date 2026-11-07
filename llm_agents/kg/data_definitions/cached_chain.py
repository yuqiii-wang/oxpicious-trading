"""CachedChain — data definition.

Migrated verbatim from :mod:`llm_agents.kg.query_log` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Tuple


@dataclass(frozen=True)
class CachedChain:
    """One logged kg_search result chain — the rows of a chain_id in
    llm.kg_queries (or its promoted copy in llm.kg_hotpaths), edge
    order preserved (each row's depth is its 0-based ordinal in the
    chain)."""

    chain_id: str
    created_at: datetime
    edge_ids: Tuple[int, ...]
