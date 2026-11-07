"""llm_agents.deep_agents.planning.planner.assembler — the plan node's
USER-MESSAGE BUILDER (:class:`PlanPromptAssembler`).

The BUSINESS framing of the run's input (scope subjects / All Market,
the focus question, the selected skills, the analysis items), the
loaded plan prompts, and the not-yet-loaded skills' catalog, as
methods of the object holding those five inputs. ``Planner`` builds
one per session; the slim-retry path rebuilds the FIRST user message
from it. The framing is the planner's step-NAMING input — every step
is named after it and works on it, never after a data operation.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from llm_agents.deep_agents.payload_guard import (
    guard_notes_block, guard_payload,
)
from llm_agents.deep_agents.planning.prompt_loading import CatalogRow

from llm_agents.deep_agents.planning.planner.template_args import (
    _scope_entries,
)


class PlanPromptAssembler:
    """The plan node's user-message builder (2026-11-06 — the former
    ``business_framing`` / ``catalog_block`` / ``plan_user_content``
    module functions): the BUSINESS framing of the run's input, the
    loaded plan prompts, and the not-yet-loaded skills' catalog, as
    methods of the object holding those five inputs. ``Planner`` builds
    one per session; the slim-retry path rebuilds the FIRST user
    message from it."""

    def __init__(
        self,
        goal: str,
        data: Optional[Dict[str, Any]],
        plan_instructions: List[Tuple[str, str]],
        *,
        catalog: Optional[Sequence[CatalogRow]] = None,
        label_matches: Optional[Dict[str, str]] = None,
    ) -> None:
        self._goal = goal
        self._data = data
        self._plan_instructions = list(plan_instructions or [])
        self._catalog = catalog
        self._label_matches = label_matches or {}

    def framing(self) -> str:
        """The plan node's compact BUSINESS framing of the run's input —
        what the ask is ABOUT, rendered from the structured context so the
        planner's steps name the actual subjects and metrics instead of
        generic tags: the scope subjects (the multi-pick list, or the
        explicit All Market line when the run carries no pick), the focus
        question, the selected skills/plan prompts, and the analysis items
        (label + metric count). The framing is never empty: a run with no
        scope pick carries the explicit All Market line (the raw JSON block
        still rides the message alongside it).

        With a catalog (the loading ladder's preview rows) the prompts
        line counts instead of listing: the catalog block below carries
        each key's preview, so the framing stays one line whatever the
        selection size."""
        data = self._data or {}
        lines: List[str] = []
        subjects: List[str] = []
        for scope in _scope_entries(data):
            bits = [str(scope.get("secType") or ""),
                    str(scope.get("code") or scope.get("industry") or "")]
            name = str(scope.get("name") or "").strip()
            if name:
                bits.append("\u201c" + name + "\u201d")
            subject = " ".join(b for b in bits if b)
            if subject:
                subjects.append(subject)
        if subjects:
            lines.append(
                "Scope subjects (every step works on them): "
                + "; ".join(subjects))
        else:
            # No pick = market-wide: say so EXPLICITLY (the UI's default
            # disabled tag) instead of leaving the framing silent — the
            # planner's steps then name the whole-market reading, not a
            # missing argument.
            lines.append(
                "Scope subjects (every step works on them): All Market — "
                "market-wide, every metric query spans the whole market "
                "with NO subject filter")
        question = str(data.get("question") or "").strip()
        if question:
            lines.append(f"Focus question (the user's ask): {question}")
        prompts = [str(p) for p in (data.get("prompts") or []) if str(p)]
        if prompts:
            if self._catalog is not None:
                forced = sorted(
                    k for k, v in self._label_matches.items()
                    if v in ("forced", "hotpath"))
                lines.append(
                    f"Selected skills/plan prompts: {len(prompts)} rows "
                    f"({len(self._catalog)} resolved; "
                    f"{len(forced)} keyword/hotpath-matched and force-loaded"
                    + (f": {', '.join(forced)}" if forced else "")
                    + ") — the catalog below shows what each covers")
            else:
                lines.append("Selected skills/plan prompts (fetch their texts "
                             "from llm.ai_prompts): " + ", ".join(prompts))
        items = data.get("analysis_items")
        if isinstance(items, list) and items:
            parts = []
            for item in items[:13]:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or item.get("key") or "").strip()
                if not label:
                    continue
                insight = item.get("insight")
                n_metrics = (len(insight.get("metrics") or [])
                             if isinstance(insight, dict) else 0)
                parts.append(f"{label} ({n_metrics} registered metrics)"
                             if n_metrics else label)
            if parts:
                lines.append("Analysis items to cover: " + "; ".join(parts))
        return "\n".join(lines)

    def catalog_block(self) -> str:
        """The PROMPT CATALOG block of the plan node's user message — the
        selected skills whose texts are NOT loaded, one preview line each
        (the ladder's catalog rung; keyword-FORCED and hotpath-
        corroborated rows render separately as loaded since their texts
        ride the message). Empty when the whole selection loads eagerly."""
        lines = []
        for row in self._catalog or ():
            if row.is_plan or self._label_matches.get(
                    row.prompt_key) in ("forced", "hotpath"):
                continue
            lines.append(f"- {row.prompt_key}: {row.preview_line()}")
        if not lines:
            return ""
        return ("[Prompt catalog — skills whose texts are NOT loaded; name in "
                "load_prompts ONLY the ones your steps work under, the rest "
                "stay fetchable per-step by prompt_key]\n" + "\n".join(lines))

    def user_content(self, *, slim: bool = False) -> str:
        """The plan node's user message: the goal, the BUSINESS framing of
        the structured context (then the raw JSON block — the planner gets
        the COMPACT variant of it when the caller passes one, see
        ``prompt_loading.planner_scoped_data``), the rendered texts of the
        loaded plan prompts, and — when the selection outgrew the eager
        budget — the PROMPT CATALOG of the not-yet-loaded skills whose
        ``load_prompts`` verdict the reply is asked for.

        *slim* drops the two heaviest blocks (the raw JSON context and the
        quoted plan-prompt texts), keeping the goal + the business framing
        + the catalog: the REFACTORED INPUT the proposal session falls back
        to when even its first batch overran the per-request wall-clock
        budget — the framing already names every scope subject and analysis
        item, which is all the step NAMING needs (the step hops keep the
        full data; nothing downstream reads the planner's message)."""
        parts = [self._goal]
        framing = self.framing()
        if framing:
            parts.append("[What this run is about — every step is named "
                         "after it and works on it, never after a data "
                         f"operation]\n{framing}")
        if self._data and not slim:
            # The large-payload pre-check guards the (already compact)
            # planner variant too — one full insight doc body past the
            # thresholds rides as metadata + preview, with the notes block
            # telling the planner which data the steps must fetch.
            guarded, notes = guard_payload(self._data)
            parts.append("[Structured context — JSON]\n```json\n"
                         f"{json.dumps(guarded, ensure_ascii=False, indent=2)}\n```")
            block = guard_notes_block(notes)
            if block:
                parts.append(block)
        if self._plan_instructions and not slim:
            quoted = "\n\n".join(
                f"- {key}: {instruction}"
                for key, instruction in self._plan_instructions)
            parts.append("[Plan prompts the user selected — honor their "
                         f"approach when laying out the steps]\n{quoted}")
        if self._catalog is not None:
            block = self.catalog_block()
            if block:
                parts.append(block)
        if slim:
            parts.append(
                "[Slimmed input — the raw structured context and the "
                "plan-prompt texts were DROPPED after the request overran "
                "the per-request time budget; plan from the business "
                "framing above (it names every scope subject and analysis "
                "item), propose the SMALLEST batches that name the work]")
        return "\n\n".join(parts)
