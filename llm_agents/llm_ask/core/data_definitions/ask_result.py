"""AskResult — data definition.

Migrated verbatim from :mod:`llm_agents.llm_ask.core.models` (one file per ``@dataclass``)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class AskResult:
    """Normalized ask result (the agent's unit of work)."""
    question: str
    answer: str
    provider: Optional[str] = None
    model: Optional[str] = None
    request_id: Optional[str] = None
    created: Optional[datetime.datetime] = None
    usage: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "provider": self.provider,
            "model": self.model,
            "request_id": self.request_id,
            "created": (
                self.created.isoformat() if self.created else None),
            "usage": self.usage,
        }
