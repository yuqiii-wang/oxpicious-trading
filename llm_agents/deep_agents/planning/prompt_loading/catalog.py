"""llm_agents.deep_agents.planning.prompt_loading.catalog — the
loading ladder's CATALOG face: the preview row contract, the
vocabulary normalizer, and the hotpath-precedence merge.

The catalog row (``llm.ai_prompts`` minus the instruction column —
one preview line per selected skill, the loading ladder's cheapest
rung) is what every later rung reads; the normalizer folds the
separator runs so 'commons/ma-spread' and the label 'ma spread' share
one vocabulary; the merge applies the hotpath corroboration's
precedence (forced > hotpath > matched).
"""
from __future__ import annotations

import re
from typing import Dict, Tuple

from pydantic import BaseModel, ConfigDict

# One catalog line's preview is bounded — a runaway preview column
# cannot turn the catalog into the bulk it replaces.
PREVIEW_LINE_MAX = 160


class CatalogRow(BaseModel):
    """One selected prompt's CATALOG face — everything the loading
    ladder reads BEFORE deciding to fetch the instruction text
    (``llm.ai_prompts`` minus the instruction column)."""

    model_config = ConfigDict(frozen=True)

    prompt_key: str
    sub_type: str
    prompt_type: int  # 1 skills | 2 plan (the two UI-pickable types)
    title: str
    preview: str
    version: int = 1
    bound_labels: Tuple[str, ...] = ()
    optional_labels: Tuple[str, ...] = ()
    instruction_chars: int = 0  # length only — sizes the eager budget

    @property
    def is_plan(self) -> bool:
        """Plan-type rows (prompt_type 2) shape the plan itself — they
        always load; the ladder gates only the skills (type 1)."""
        return self.prompt_type == 2

    def preview_line(self) -> str:
        """The one-line face the catalog shows: ``title — preview``,
        falling back to whichever exists, hard-bounded."""
        text = " — ".join(p for p in (self.title.strip(),
                                      self.preview.strip()) if p)
        if not text:
            text = self.prompt_key
        if len(text) > PREVIEW_LINE_MAX:
            text = text[:PREVIEW_LINE_MAX - 1] + "…"
        return text


def _normalize(text: str) -> str:
    """Lowercase with separator runs (-/_//.) folded to single spaces —
    'commons/ma-spread' and the label 'ma spread' share one vocabulary."""
    return re.sub(r"[\s/\-_.]+", " ", str(text or "").lower()).strip()


def merge_hotpath_matches(
    label_matches: Dict[str, str],
    hotpath_matches: Dict[str, str],
) -> Dict[str, str]:
    """The precedence merge of the hotpath probe's verdicts into the
    keyword matches: ``forced`` (a bound-label hit — the higher
    contract) stays forced; a ``matched`` row (question-literal
    optional hit, planner-discretionary) UPGRADES to ``hotpath`` (the
    evidence lane loads it); keys with no keyword verdict are added as
    ``hotpath``. Pure — session.py merges right after the catalog rung."""
    merged = dict(label_matches)
    for key, verdict in hotpath_matches.items():
        if merged.get(key) != "forced":
            merged[key] = verdict
    return merged
