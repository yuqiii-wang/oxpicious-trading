"""ScoredNode — data definition.

Migrated verbatim from :mod:`llm_agents.kg.graph.values` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoredNode:
    """One result node with its provenance line."""

    entity: KgEntity
    score: float
    why: str
