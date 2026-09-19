"""llm_agents.llm_ask.core.models — Normalized result models.

Provider-independent dataclasses used by every provider and the CLI,
mirroring the models layer of ``llm_agents.online_search_summary.core.
models`` minus the search vocabulary — this package sends plain LLM chat
completions only, so there are no hits, no citations, and no search
options. Nothing here touches the network or the DB.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from llm_agents._core.models import SHANGHAI_TZ

# Shared "today" reference for prompt anchors — the corpus and the market
# calendar it serves are Asia/Shanghai (re-export of the llm_agents._core
# constant).


@dataclass
class AskOptions:
    """Provider-agnostic ask knobs (passed through to the provider)."""
    model: Optional[str] = None          # None -> provider default
    lang: str = "zh"                     # answer language
    system: Optional[str] = None         # None -> lang-aware default prompt
    temperature: Optional[float] = None  # None -> provider default
    request_id: Optional[str] = None
    # Optional images (PNG data URLs) attached to the user message —
    # sent only when the model accepts image input (core.vision);
    # otherwise dropped and the ask proceeds text-only.
    images: Tuple[str, ...] = ()

    def validate(self) -> None:
        if self.temperature is not None and not (
                0.0 <= self.temperature <= 1.0):
            raise ValueError("temperature must be within [0, 1], got "
                             f"{self.temperature}")
        for image in self.images:
            if not isinstance(image, str) or not image.startswith("data:image/"):
                raise ValueError(
                    "images must be image data URLs "
                    "('data:image/png;base64,…'), got "
                    f"{image[:40]!r}")


@dataclass
class ChatCompletion:
    """One raw chat-completion reply, provider fields normalized."""
    content: str = ""
    request_id: Optional[str] = None
    created: Optional[datetime.datetime] = None
    usage: Optional[Dict[str, Any]] = None


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
