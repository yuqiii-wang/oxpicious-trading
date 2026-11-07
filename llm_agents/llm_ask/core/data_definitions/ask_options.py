"""AskOptions — data definition.

Migrated verbatim from :mod:`llm_agents.llm_ask.core.models` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


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
