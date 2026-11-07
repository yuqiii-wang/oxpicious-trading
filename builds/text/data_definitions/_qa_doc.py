"""_QaDoc — data definition.

Migrated verbatim from :mod:`builds.text.ai_daily_qa` (one file per ``@dataclass``)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import List, Optional
from llm_agents.online_search_summary.core.models import SearchHit


LANGUAGE = "zh"


@dataclass
class _QaDoc:
    """One storable Q&A (the general summary, one movers ask or one
    geopolitics/economy ask)."""
    question: str
    answer: str
    qa_date: datetime.datetime          # the ask's data date, SH midnight
    industry_id: Optional[str]          # None -> broad-market tag at sync
    hits: List[SearchHit]
    llm_model: Optional[str]
    language: str = LANGUAGE            # the answer's language (en for
