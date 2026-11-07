"""SeedHit — data definition.

Migrated verbatim from :mod:`llm_agents.kg.graph.values` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class SeedHit:
    """Why an entity is a SEED for the query."""

    score: float
    terms: Tuple[Tuple[str, str], ...]   # ((token, face_kind), …) best face per token
