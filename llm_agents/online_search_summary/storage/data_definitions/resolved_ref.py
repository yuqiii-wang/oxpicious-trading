"""ResolvedRef — data definition.

Migrated verbatim from :mod:`llm_agents.online_search_summary.storage.resolve` (one file per ``@dataclass``)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, Optional
from llm_agents.online_search_summary.storage.matching import VIA_SUMMARY


@dataclass
class ResolvedRef:
    """Outcome of resolving one citation tag to a text.news article."""
    ref: str                          # citation tag (ref_N)
    ref_type: str                     # exact (from summary) | relevant (post-hoc title match)
    news_id: int
    resolved_url: Optional[str] = None  # candidate URL the check ran against
    ref_time: Optional[datetime.datetime] = None  # actual publish time (None -> stored NULL)
    via: str = VIA_SUMMARY            # provenance: summary | corpus | ddgs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref,
            "ref_type": self.ref_type,
            "news_id": self.news_id,
            "resolved_url": self.resolved_url,
            "ref_time": (self.ref_time.isoformat() if self.ref_time
                         else None),
            "via": self.via,
        }
