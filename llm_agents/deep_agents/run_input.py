"""llm_agents.deep_agents.run_input — the run's structured-input BOUNDARY
models (2026-11-06): the typed face of ``data`` where a run enters the
harness (the daemon's POST /run, the CLI's --data/--data-file), anchored
by :meth:`RunInput.validate_boundary` — validate what these models know,
keep the unknown keys, hand the run the SAME plain-dict shape downstream
(the graph's duck-typed reads are unchanged; a malformed known field
degrades to the raw value with a log — the same fail-soft contract as
every boundary here).

stdlib + pydantic only (the limits.py layering rule — every layer and
both entrypoints may import it; nothing from the package).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("deep_agents.run_input")


class ScopePick(BaseModel):
    """One classification-nav scope pick (the analysis-summary bridge's
    ``{"secType", "code" | "industry", "name"?}`` entry) — the typed
    face of the run's subject list (``data.scopes``). Extra keys pass
    through (the bridge may attach context)."""

    model_config = ConfigDict(extra="allow")

    secType: str = ""
    code: str = ""
    industry: str = ""
    name: str = ""


class RunInput(BaseModel):
    """The run's structured input ``data``: the scope picks, the focus
    question, the analysis items, the picked prompts and the run-scoped
    label overrides. Unknown keys pass through untouched (the deep lane
    reads what it knows)."""

    model_config = ConfigDict(extra="allow")

    scopes: List[ScopePick] = Field(default_factory=list)
    question: str = ""
    analysis_items: List[Dict[str, Any]] = Field(default_factory=list)
    prompts: List[Any] = Field(default_factory=list)
    prompt_labels: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def validate_boundary(cls, data: Optional[Dict[str, Any]],
                          ) -> Optional[Dict[str, Any]]:
        """The boundary anchor: validate what this model knows, keep the
        unknown keys (``extra="allow"`` rides them through
        ``model_dump``), and hand the run the plain dict. A malformed
        known field degrades to the raw dict with a log."""
        if data is None:
            return None
        try:
            model = cls.model_validate(data)
        except Exception as exc:  # noqa: BLE001 — never fail the run
            logger.warning("run data boundary: %s — the raw dict rides "
                           "as-is", exc)
            return data
        return model.model_dump()
