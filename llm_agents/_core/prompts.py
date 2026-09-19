"""llm_agents._core.prompts — Shared prompt primitives.

The language-label vocabulary shared by every llm_agents prompt builder;
the per-agent ``core.prompts`` modules re-export it so existing import
paths keep working. Pure string building — no network, no DB.
"""
from __future__ import annotations

# lang code -> human label used inside prompts; unknown codes pass through.
LANG_LABELS = {"zh": "中文", "en": "English"}


def lang_label(lang: str) -> str:
    return LANG_LABELS.get((lang or "zh").lower(), lang or "中文")
