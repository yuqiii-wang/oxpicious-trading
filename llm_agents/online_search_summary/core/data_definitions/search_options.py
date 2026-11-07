"""SearchOptions — data definition.

Migrated verbatim from :mod:`llm_agents.online_search_summary.core.models` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


CONTENT_SIZES = ("medium", "high")


RECENCY_FILTERS = ("oneDay", "oneWeek", "oneMonth", "oneYear", "noLimit")


@dataclass
class SearchOptions:
    """Provider-agnostic search knobs (passed through to the provider)."""
    engine: Optional[str] = None      # None -> provider default
    count: int = 10
    recency: str = "noLimit"
    domain: Optional[str] = None      # restrict to one site
    content_size: str = "medium"      # snippet length: medium | high
    intent: bool = True               # standalone search: recognize intent
    request_id: Optional[str] = None

    def validate(self) -> None:
        if not 1 <= self.count <= 50:
            raise ValueError(f"count must be 1-50, got {self.count}")
        if self.recency not in RECENCY_FILTERS:
            raise ValueError(f"recency must be one of {RECENCY_FILTERS}, "
                             f"got {self.recency!r}")
        if self.content_size not in CONTENT_SIZES:
            raise ValueError(f"content_size must be one of {CONTENT_SIZES}, "
                             f"got {self.content_size!r}")
