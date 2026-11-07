"""SearchIntent — data definition.

Migrated verbatim from :mod:`llm_agents.online_search_summary.core.models` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SearchIntent:
    """One rewritten query from the provider's intent recognition."""
    query: Optional[str] = None
    intent: Optional[str] = None     # e.g. SEARCH_ALL / SEARCH_NONE
    keywords: Optional[str] = None
