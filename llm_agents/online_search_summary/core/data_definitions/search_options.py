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
    # POST-SEARCH ref pre-filter (the recency DOUBLE CHECK — the engine's
    # own recency knob is coarse and, on noLimit, lets years-old
    # SEO-dominant pages rank in): drop references whose known publish
    # date is older than this many days BEFORE the summary LLM request
    # sees them (survivors renumbered so citation tags stay gapless).
    # 0 disables (keep every hit). NOT sent to the provider — applied on
    # our side over the returned hits.
    ref_recency_days: int = 0

    def validate(self) -> None:
        if not 1 <= self.count <= 50:
            raise ValueError(f"count must be 1-50, got {self.count}")
        if self.recency not in RECENCY_FILTERS:
            raise ValueError(f"recency must be one of {RECENCY_FILTERS}, "
                             f"got {self.recency!r}")
        if self.content_size not in CONTENT_SIZES:
            raise ValueError(f"content_size must be one of {CONTENT_SIZES}, "
                             f"got {self.content_size!r}")
        if not 0 <= self.ref_recency_days <= 3650:
            raise ValueError(f"ref_recency_days must be 0 (off) or 1-3650, "
                             f"got {self.ref_recency_days}")
