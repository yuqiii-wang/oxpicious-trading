"""llm_agents.llm_ask.core.vision — Image-capability config per model.

THE config deciding which chat models accept image input and which do
not. ``ask`` consults it before attaching images: with a vision-capable
model the user message becomes multimodal (text + image_url parts); with
a text-only model the images are DROPPED with a warning log and the ask
proceeds text-only — image input is always optional, never fatal.

Per provider:

  * ``VISION_MODELS``           — exact model names with image input
    (keep in sync with the provider's model catalog).
  * ``VISION_MODEL_SUFFIXES``   — family naming conventions: ZhiPu's
    multimodal line carries a trailing "v" (glm-4v / glm-4.5v / glm-4.6v
    / a future glm-5v …), so any such name qualifies without editing
    this file. Text models (glm-4.5, glm-5.2, …) never match.
  * ``DEFAULT_VISION_MODELS``   — the vision model ``--payload-file``
    asks (the data_viz AI Ask chart adviser) switch to when the payload
    carries screenshots and no explicit ``--model`` was given.

Pure lookup tables — no network, no DB.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Optional, Tuple

# ZhiPu BigModel multimodal chat models (open.bigmodel.cn), fed through
# the same chat/completions endpoint as image_url content parts.
_ZHIPU_VISION_MODELS: FrozenSet[str] = frozenset({
    "glm-4v", "glm-4v-plus", "glm-4v-flash",
    "glm-4.1v-thinking", "glm-4.5v", "glm-4.6v",
})

VISION_MODELS: Dict[str, FrozenSet[str]] = {
    "zhipu": _ZHIPU_VISION_MODELS,
}

VISION_MODEL_SUFFIXES: Dict[str, Tuple[str, ...]] = {
    # glm-<version>v — the multimodal family convention.
    "zhipu": ("v",),
}

DEFAULT_VISION_MODELS: Dict[str, str] = {
    # glm-4.6v — the strongest vision model on this key (glm-4.5v /
    # glm-4v hit error 1113 "no balance / resource pack" on the project
    # account; glm-4v-flash also works as the free fallback).
    "zhipu": "glm-4.6v",
}


def model_accepts_images(provider: str, model: str) -> bool:
    """True iff ``model`` accepts image input on ``provider``."""
    return (model in VISION_MODELS.get(provider, frozenset())
            or model.endswith(VISION_MODEL_SUFFIXES.get(provider, ())))


def default_vision_model(provider: str) -> Optional[str]:
    """Provider's vision model for image-carrying asks (None = none)."""
    return DEFAULT_VISION_MODELS.get(provider)
