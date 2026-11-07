"""llm_agents.online_search_summary.core.models — Normalized result models.

Provider-independent dataclasses + the shared request vocabulary used by
every provider and by the persistence layer. Parsing/formatting of the
citation markers lives in ``core.citations``; nothing here touches the
network or the DB.

Shared request vocabulary: ``RECENCY_FILTERS`` / ``CONTENT_SIZES`` are the
ZhiPu values carried by ``SearchOptions``; future providers translate them
to their own equivalents inside their subclass.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from llm_agents._core.models import SHANGHAI_TZ
from llm_agents.online_search_summary.core.citations import extract_cited_refs

from llm_agents.online_search_summary.core.data_definitions import (
    SearchHit,
    SearchIntent,
    SearchOptions,
    SearchResponse,
    SearchSummary,
    CONTENT_SIZES,
    RECENCY_FILTERS,
)
# Shared "today" reference for fallback dates and prompt anchors — the
# corpus and the market calendar it serves are Asia/Shanghai (re-export
# of the llm_agents._core constant).
