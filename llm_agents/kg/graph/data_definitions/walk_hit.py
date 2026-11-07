"""WalkHit — data definition.

Migrated verbatim from :mod:`llm_agents.kg.graph.values` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class WalkHit:
    """The spreading-activation trace of one propagated node."""

    activation: float
    origin_id: int                        # the seed the best path started from
    hops: int
    path: Tuple[Tuple[int, int], ...]     # ((edge_id, other_member_id), …) in walk order
