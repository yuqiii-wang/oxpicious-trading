"""GeoAsk — data definition.

Migrated verbatim from :mod:`llm_agents.online_search_summary.daily` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from llm_agents.online_search_summary.core.models import SearchOptions


@dataclass(frozen=True)
class GeoAsk:
    """One daily geopolitics/economy ask: what to search and in which
    language the answer must be written."""
    scope: str               # GEO_SCOPE_GLOBAL | GEO_SCOPE_CHINA
    lang: str                # answer language — "en" | "zh"
    query: str
    opts: SearchOptions
