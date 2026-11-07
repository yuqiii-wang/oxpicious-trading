"""HotpathChain — data definition.

Migrated verbatim from :mod:`llm_agents.kg.hotpaths` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from .cached_chain import CachedChain


@dataclass(frozen=True)
class HotpathChain(CachedChain):
    """One promoted hot-path chain — the llm.kg_hotpaths rows of a
    chain_id, edge order preserved (each row's depth is its 0-based
    ordinal in the chain), plus the chain's hit_count (times this
    (query, chain) has been served)."""

    hit_count: int
