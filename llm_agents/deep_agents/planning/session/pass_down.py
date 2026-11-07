"""llm_agents.deep_agents.planning.session.pass_down — the plan
session's PASS-DOWN pieces: the ladder's outcome carrier, the catalog
mirror, the fork pass-down, and the plan payload's steps array.

These are the pure shapes the session hands DOWNWARD — to every step
hop's conversation (the loaded texts + the not-loaded catalog), to
every forked child (the run context + goal + the same prompt faces),
and to the plan payload (the steps array both planning lanes persist).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from llm_agents.deep_agents.planning.prompt_loading import CatalogRow
from llm_agents.deep_agents.planning.store import (
    PlannedStep, PromptCatalogRow,
)


class PromptBudget(BaseModel):
    """The ladder's outcome: the LOADED set's rendered texts (the
    pass-down every step hop's conversation and every forked child
    receives) and the NOT-loaded rows' catalog lines (prompt_key ->
    one preview line — the per-step fetch menu)."""

    texts: Dict[str, str] = Field(default_factory=dict)
    catalog: Dict[str, str] = Field(default_factory=dict)


def _catalog_mirror(
    rows: List[PromptCatalogRow],
) -> List[CatalogRow]:
    """The store's catalog rows as the pure ladder's mirror type."""
    return [
        CatalogRow(
            prompt_key=r.prompt_key, sub_type=r.sub_type,
            prompt_type=r.prompt_type, title=r.title, preview=r.preview,
            version=r.version, bound_labels=r.bound_labels,
            optional_labels=r.optional_labels,
            instruction_chars=r.instruction_chars)
        for r in rows
    ]


def item_labels(data: Optional[Dict[str, Any]]) -> List[str]:
    """The analysis items' label/key vocabulary — the keyword rung's
    item-side haystack (an item's own name is the strongest signal a
    same-domain skill applies)."""
    items = (data or {}).get("analysis_items")
    if not isinstance(items, list):
        return []
    labels: List[str] = []
    for item in items:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("key") or "").strip()
            if label:
                labels.append(label)
    return labels


class ForkPassDown(BaseModel):
    """The PARENT'S PASS-DOWN for every forked subagent (2026-11-06 —
    the former ``fork_biz_context`` dict): the run's structured context
    (the model's extra fields) plus the run goal, the rendered texts of
    the LOADED prompts, and — when the ladder left rows unloaded —
    their catalog lines (prompt_key -> preview; the child fetches one
    by key when its fork works that skill's domain) — what the fork's
    work runs under, stamped on ``SubagentRequest.data`` and rendered
    into the child's first user message beside its fork goal."""

    model_config = ConfigDict(extra="allow")

    goal: str = ""
    prompt_instructions: Dict[str, str] = Field(default_factory=dict)
    prompt_catalog: Dict[str, str] = Field(default_factory=dict)

    @classmethod
    def pack(
        cls,
        goal: str,
        data: Optional[Dict[str, Any]],
        prompt_texts: Dict[str, str],
        prompt_catalog: Optional[Dict[str, str]] = None,
    ) -> "ForkPassDown":
        """The pass-down from the parent's inputs — the structured
        context as the model's extra fields, the three pass-down keys
        set only when non-empty (absent on the wire, as before)."""
        return cls(
            **(data or {}),
            goal=goal or "",
            prompt_instructions=dict(prompt_texts or {}),
            prompt_catalog=dict(prompt_catalog or {}),
        )

    def to_context(self) -> Dict[str, Any]:
        """The context dict the child's message builder reads — the
        pass-down keys absent when empty (the exact shape
        ``fork_biz_context`` produced)."""
        return self.model_dump(exclude_none=True)


def plan_payload_steps(
    steps: List[PlannedStep],
) -> List[Dict[str, Any]]:
    """The plan payload's steps array — node/title/notes plus the fork
    goal verbatim (the audit trail of what the planner delegated) and
    the parallel mark (the wave assertion rides the payload, so a
    Thread-Save templating of it keeps the wave — the one shape both
    planning lanes (LLM proposal and saved template) persist)."""
    return [
        {
            "node": s.node, "title": s.title, "notes": s.notes,
            **({"delegate": {"goal": s.delegate}} if s.delegate else {}),
            **({"parallel": True} if s.parallel else {}),
        }
        for s in steps
    ]
