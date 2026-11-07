"""ChatCompletion — data definition.

Migrated verbatim from :mod:`llm_agents.llm_ask.core.models` (one file per ``@dataclass``)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ChatCompletion:
    """One raw chat-completion reply, provider fields normalized."""
    content: str = ""
    request_id: Optional[str] = None
    created: Optional[datetime.datetime] = None
    usage: Optional[Dict[str, Any]] = None
