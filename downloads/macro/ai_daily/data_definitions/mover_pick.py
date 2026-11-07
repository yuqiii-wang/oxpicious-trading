"""MoverPick — data definition.

Migrated verbatim from :mod:`downloads.macro.ai_daily.movers` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class MoverPick:
    """One candidate mover (post-gate, pre/post cooldown)."""
    industry_id: str
    label: str
    offset: float
    side: str                      # rise | drop
    rank: int                      # 1-based within its side
    question: str = ""
    cooldown: bool = False         # True = suppressed by the 5d cooldown

    def to_dict(self) -> Dict[str, Any]:
        return {
            "industry_id": self.industry_id, "label": self.label,
            "offset": self.offset, "side": self.side, "rank": self.rank,
            "question": self.question, "cooldown": self.cooldown,
        }
