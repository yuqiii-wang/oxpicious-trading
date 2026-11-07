"""SearchHit — data definition.

Migrated verbatim from :mod:`llm_agents.online_search_summary.core.models` (one file per ``@dataclass``)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SearchHit:
    """One normalized search-result reference."""
    refer: str                        # canonical citation tag (ref_N)
    title: str
    content: Optional[str] = None     # page summary / snippet
    link: Optional[str] = None
    media: Optional[str] = None       # source site name
    icon: Optional[str] = None        # site favicon URL
    publish_date: Optional[datetime.date] = None
    publish_date_raw: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "refer": self.refer,
            "title": self.title,
            "content": self.content,
            "link": self.link,
            "media": self.media,
            "icon": self.icon,
            "publish_date": (
                self.publish_date.isoformat() if self.publish_date else None),
            "publish_date_raw": self.publish_date_raw,
        }
