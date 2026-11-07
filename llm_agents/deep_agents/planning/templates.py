"""llm_agents.deep_agents.planning.templates — SAVED LANGGRAPH TEMPLATES,
the zero-LLM graph lanes.

A plan that worked is worth reusing: the UI's Thread Graph Save button
copies a FINISHED run's plan JSON into
``llm.deep_agent_langgraph_templates`` (DDL ``database/sql/llm/
11_deep_agent_langgraph_templates.sql`` — renamed from
deep_agent_plan_templates 2026-11-06, the three-graph refactor) with
the run-specific variable parts replaced by ``{{key}}`` placeholders —
the SAME vocabulary the prompt templates use
(``planner.template_args``: ``{{question}}`` / ``{{code}}`` /
``{{name}}`` / ``{{sec_type}}``; the templating itself happens
bridge-side, ``data_viz/api/services/ai-plan-template.service.ts``).
This module owns the RUNNER side of the reuse, per template TYPE
(static.label_dim domain 'langgraph_type'), as ONE service object:

  * type 0 plan — :meth:`TemplateLibrary.fetch_plan_template` +
    :meth:`TemplateLibrary.materialize_plan_steps`: render the
    template's placeholders with the NEW run's input args
    (:func:`planner.render_template` — known keys always substitute,
    empty string when the run carries none) and build the validated
    :class:`PlannedStep` list the plan session persists as the CUSTOM
    graph's step rows — planning finishes with ZERO planning LLM calls.
    (Saved plans are the BUSINESS MIDDLE only — the reviews and the
    final synthesis belong to the run's separate RESULT graph.)
  * type 2 result — :meth:`TemplateLibrary.fetch_result_template` +
    :meth:`TemplateLibrary.materialize_result_steps`: the RESULT
    graph's fixed four-review-wave definition (the SEEDED
    ``default-result-wave`` row; the review goals are DB-editable),
    materialized into :class:`PlannedStep` seats; the caller falls back
    to the built-in ``plan_json.review_wave_steps`` fail-soft.
  * type 1 custom — reserved vocabulary (a saved custom-graph step
    set); no runner lane reads it yet.

Fail-soft as everywhere in the harness: a missing or malformed
template raises here and the RUNNER falls back (the plan lane to the
normal LLM proposal — planning never fails a run; the result lane to
the built-in wave constants).

A template's steps were already validated the day they were planned, so
materialization does NOT re-run the ops-name rejection
(``plan_json.parse_plan_json``) — rendering only substitutes subject/
question words, and a false rejection would pointlessly degrade a
perfectly good template. The per-step field extraction (delegate /
parallel marks) is shared with the plan-JSON contract; the plan-step
caps stay as sanity clamps.

Pure domain like the rest of ``planning/``: no LangGraph, no provider —
only this repo's plan-store row types and the template renderer. The
former module functions are methods of one process-wide
:data:`TEMPLATE_LIBRARY` (2026-11-06); the template rows are typed
(:class:`PlanTemplate` / :class:`ResultTemplate`).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.plan_json import (
    MAX_NODE_LEN, MAX_TITLE_LEN, _parallel_mark, _review_fixup,
    _step_delegate,
)
from llm_agents.deep_agents.planning.planner import render_template
from llm_agents.deep_agents.planning.store import PlannedStep

logger = logging.getLogger("deep_agents.templates")

# static.label_dim domain 'langgraph_type' — kept literal here (the
# planning layer's label use is int codes resolved at the SQL boundary
# elsewhere; this module only needs the two closed-vocabulary filters).
TEMPLATE_TYPE_PLAN = 0
TEMPLATE_TYPE_RESULT = 2


class PlanTemplate(BaseModel):
    """One saved PLAN template row (type 0) — the Save button's
    placeholder-templated copy of a finished run's plan."""

    model_config = ConfigDict(frozen=True)

    template_id: int
    name: str
    thread_id: Optional[str] = None
    args: Dict[str, Any] = {}
    plan_json: Dict[str, Any] = {}


class ResultTemplate(BaseModel):
    """The RESULT graph's template row (type 2) — the DB-editable
    four-review-wave definition (normally the seeded
    ``default-result-wave`` row)."""

    model_config = ConfigDict(frozen=True)

    template_id: int
    name: str
    plan_json: Dict[str, Any] = {}


class TemplateLibrary:
    """The RUNNER side of saved-template reuse (2026-11-06 — the former
    module functions): the fetches (one plain accessor each, raising on
    DB failure — the callers wrap fail-soft) and the materializers, as
    methods of one process-wide :data:`TEMPLATE_LIBRARY`."""

    @staticmethod
    def _render_strings(value: Any, args: Dict[str, str]) -> Any:
        """Recursively render ``{{key}}`` placeholders in every STRING of a
        JSON-ish value (the plan payload's steps carry nested notes dicts and
        delegate goals); non-strings pass through untouched."""
        if isinstance(value, str):
            return render_template(value, args)
        if isinstance(value, list):
            return [TemplateLibrary._render_strings(item, args)
                    for item in value]
        if isinstance(value, dict):
            return {key: TemplateLibrary._render_strings(item, args)
                    for key, item in value.items()}
        return value

    def _materialize_steps(
        self,
        plan_json: Dict[str, Any],
        args: Dict[str, str],
    ) -> List[PlannedStep]:
        """One saved template's payload, rendered for a NEW run: every
        string of its ``steps`` array renders its ``{{key}}`` placeholders
        with the run's input args, then each entry becomes a
        :class:`PlannedStep` — the same field extraction the plan-JSON
        parser applies (delegate / parallel marks included), without the
        ops-name rejection a template already passed the day it was
        planned. The step/fork caps clamp as sanity. Raises
        :class:`ValueError` when the payload carries no usable steps (the
        runner's fallback)."""
        raw_steps = (plan_json.get("steps")
                     if isinstance(plan_json, dict) else None)
        if not isinstance(raw_steps, list):
            raise ValueError("template plan_json has no 'steps' list")
        steps: List[PlannedStep] = []
        delegated = 0
        limits = run_limits()
        for raw in raw_steps[:limits.max_plan_nodes]:
            if not isinstance(raw, dict):
                continue
            rendered = self._render_strings(raw, args)
            title = str(rendered.get("title") or "").strip()
            if not title:
                continue
            delegate = _step_delegate(rendered)
            if delegate is not None:
                delegated += 1
                if delegated > limits.max_delegated_steps:
                    delegate = None
            steps.append(PlannedStep(
                node=(str(rendered.get("node") or "").strip()
                      or f"step_{len(steps) + 1}")[:MAX_NODE_LEN],
                title=title[:MAX_TITLE_LEN],
                notes=rendered.get("notes"),
                delegate=delegate,
                parallel=_parallel_mark(rendered),
            ))
        if not steps:
            raise ValueError("template plan_json has no titled steps")
        return steps

    def materialize_plan_steps(
        self,
        plan_json: Dict[str, Any],
        args: Dict[str, str],
    ) -> List[PlannedStep]:
        """One saved PLAN template's (type 0) payload, rendered for a NEW
        run — the custom graph's business middle (the reviews and the
        final synthesis belong to the run's separate RESULT graph). See
        :meth:`_materialize_steps` for the shared mechanics."""
        return self._materialize_steps(plan_json, args)

    @staticmethod
    def _jsonb_dict(value: Any) -> Dict[str, Any]:
        """One JSONB column's value as a dict — asyncpg hands JSONB back as
        the raw JSON STRING (no codec on this connection path), so parse
        first (state_store's json.loads convention); anything that is not a
        JSON object degrades to {} (the runner's fail-soft fallback)."""
        if isinstance(value, dict):
            return value
        if isinstance(value, (str, bytes)):
            try:
                parsed = json.loads(value)
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    async def fetch_plan_template(
        self,
        template_id: int,
    ) -> Optional[PlanTemplate]:
        """One saved PLAN template row (type 0) by id — or None when no
        such type-plan row exists (a type-result/custom row is not
        loadable as a plan). Plain accessor (raises on DB failure); the
        runner wraps it fail-soft."""
        from _common.db_commons import get_db_connection_async

        conn = await get_db_connection_async()
        try:
            record = await conn.fetchrow(
                "SELECT template_id, name, thread_id, args, plan_json "
                "FROM llm.deep_agent_langgraph_templates "
                "WHERE template_id = $1 AND type = $2",
                int(template_id), TEMPLATE_TYPE_PLAN,
            )
        finally:
            await conn.close()
        if record is None:
            return None
        return PlanTemplate(
            template_id=int(record[0]),
            name=str(record[1]),
            thread_id=record[2],
            args=self._jsonb_dict(record[3]),
            plan_json=self._jsonb_dict(record[4]),
        )

    async def fetch_result_template(self) -> Optional[ResultTemplate]:
        """The RESULT graph's template row (type 2, most recently updated)
        — or None when the table carries none (the caller falls back to
        the built-in ``plan_json.review_wave_steps`` constants; the
        seeded ``default-result-wave`` row normally answers). Plain
        accessor (raises on DB failure); the runner wraps it fail-soft."""
        from _common.db_commons import get_db_connection_async

        conn = await get_db_connection_async()
        try:
            record = await conn.fetchrow(
                "SELECT template_id, name, plan_json "
                "FROM llm.deep_agent_langgraph_templates "
                "WHERE type = $1 "
                "ORDER BY updated_at DESC, template_id DESC LIMIT 1",
                TEMPLATE_TYPE_RESULT,
            )
        finally:
            await conn.close()
        if record is None:
            return None
        return ResultTemplate(
            template_id=int(record[0]),
            name=str(record[1]),
            plan_json=self._jsonb_dict(record[2]),
        )

    def materialize_result_steps(
        self,
        plan_json: Dict[str, Any],
    ) -> List[PlannedStep]:
        """The RESULT template's review seats: rendered with EMPTY args
        (the fixed wave carries no run placeholders), each seat normalized
        through the review fixup (parallel forced, delegate stripped — a
        reviewer reads the findings bridge, so it stays inline). Raises
        :class:`ValueError` when the payload carries no usable steps (the
        caller's fallback: the built-in wave constants)."""
        seats = self._materialize_steps(plan_json, {})
        return [_review_fixup(seat) for seat in seats]


# The process-wide library (the module functions became its methods,
# 2026-11-06).
TEMPLATE_LIBRARY = TemplateLibrary()
