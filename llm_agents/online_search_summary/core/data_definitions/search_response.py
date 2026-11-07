"""SearchResponse — data definition.

Migrated verbatim from :mod:`llm_agents.online_search_summary.core.models` (one file per ``@dataclass``)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SearchResponse:
    """Normalized standalone-search response."""
    hits: List[SearchHit] = field(default_factory=list)
    intents: List[SearchIntent] = field(default_factory=list)
    provider: Optional[str] = None
    engine: Optional[str] = None
    request_id: Optional[str] = None
    created: Optional[datetime.datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "engine": self.engine,
            "request_id": self.request_id,
            "created": (
                self.created.isoformat() if self.created else None),
            "intents": [vars(i) for i in self.intents],
            "references": [h.to_dict() for h in self.hits],
        }
