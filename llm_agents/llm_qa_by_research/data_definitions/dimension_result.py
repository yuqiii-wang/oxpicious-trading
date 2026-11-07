"""DimensionResult — data definition.

Migrated verbatim from :mod:`llm_agents.llm_qa_by_research.generation` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
from llm_agents.online_search_summary.core.models import SearchHit
from llm_agents.online_search_summary.storage.resolve import ResolvedRef
from .dimension_spec import DimensionSpec


@dataclass
class DimensionResult:
    """One dimension request's outcome (text None = the request failed).

    ``resolved`` carries the dimension's hits fast-resolved into
    text.news rows (the request-time resolution that also binds the
    llm.llm_requests ref ids); empty when the search surfaced nothing
    or resolution failed (refs stay non-fatal).
    """
    spec: DimensionSpec
    text: Optional[str] = None
    hits: List[SearchHit] = field(default_factory=list)
    resolved: List[ResolvedRef] = field(default_factory=list)
