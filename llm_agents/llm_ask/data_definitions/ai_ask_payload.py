"""AiAskPayload — data definition.

Migrated verbatim from :mod:`llm_agents.llm_ask.payload` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class AiAskPayload:
    """One validated AI Ask request from the Express ai-ask service."""

    question: str
    plot_info: Dict[str, Any]
    screenshots: Tuple[str, ...] = ()
    theme_mode: Optional[str] = None
    online_search: bool = False
    # Web-search term (the modal's search line, seeded with chart title +
    # date) — searched instead of the question; None falls back to it.
    search_query: Optional[str] = None
