"""DimensionSpec — data definition.

Migrated verbatim from :mod:`llm_agents.llm_qa_by_research.prompts` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DimensionSpec:
    """One evaluation dimension: its column and its question contract."""
    key: str                 # storage kwarg / registry identity
    column: str              # llm.llm_qa_by_research column
    title: str               # zh display title (UI section header)
    ask: str                 # the dimension QUESTION template ({subject})
    search_suffix: str       # retrieval terms appended to the search query
