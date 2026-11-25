"""ResolvedToken — data definition.

One query token resolved against the token index (the seed rung's
input, 2026-11-23)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ResolvedToken:
    """One query token resolved against the token index: the bucket it
    seeds from (entity_id → the face weight it matched at), the score
    factor its hits carry (1.0 for an exact match), and — for a FUZZY
    match — the vocabulary token it mapped onto (``None`` when exact).
    A token that matched nothing is not resolved at all."""

    token: str
    bucket: Dict[int, float]
    factor: float
    matched: Optional[str]

    @property
    def is_fuzzy(self) -> bool:
        return self.matched is not None
