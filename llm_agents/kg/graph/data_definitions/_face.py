"""_Face — data definition.

Migrated verbatim from :mod:`llm_agents.kg.graph.engine` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Face:
    """One matchable text face of an entity (kind + folded text +
    weight). Built once at graph construction."""

    kind: str    # 'name' | 'tag' | 'description' | 'type'
    text: str
    weight: float
