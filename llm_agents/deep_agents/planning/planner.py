"""llm_agents.deep_agents.planning.planner — the PLAN PROPOSAL SESSION
as an independent class.

The deep-agent run's plan node asks the model to translate its intent
into the states JSON the runner compiles the thread from
(``{"steps": [{"node", "title", "notes", "delegate"?}]}`` — the
contract in ``plan_json``). This module owns that whole conversation
as ONE class, :class:`Planner`:

  * it assembles what the model reads — the DYNAMIC-TEMPLATE
    machinery (``template_args`` / ``render_template``, moved here
    from graph.prompts so planning is self-contained), the compact
    BUSINESS framing of the run's input (``business_framing``: scope
    subject, focus question, selected skills, analysis items), and the
    rendered texts of the selected plan prompts
    (``plan_user_content``);
  * it calls the LLM and validates the reply against the plan-JSON
    contract (``plan_json.parse_plan_json`` — structure AND business
    naming); on ANY rejection (no JSON object, malformed JSON, missing
    titles, ops-named steps, ...) the SPECIFIC complaint goes BACK to
    the model as a correction turn — its own bad reply plus the repair
    instruction — up to *repair_rounds* corrections after the first
    attempt;
  * ``propose()`` returns ONLY steps that validated: the
    guaranteed-compile-able thread states (the runner persists them
    one-to-one as the pending ``text.deep_agent_states`` rows the step
    lane walks). After the last rejected round it raises
    :class:`PlanJsonError`; transport errors propagate — the runner's
    fail-soft wrapper degrades to the plain execute lane either way,
    never failing a run over a plan.

Pure domain: no DB, no LangGraph, no provider import — the chat
callable rides in duck-typed (anything async with
``chat_complete(messages, model=, temperature=)``), so a session
unit-tests against a stub. ``plan_reply_steps`` keeps the older
functional form (a pre-built message list) as a thin wrapper over the
class, so both entry points share the one loop.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from llm_agents.deep_agents.planning.plan_json import (
    PLAN_SYSTEM_PROMPT, PlanJsonError, parse_plan_json,
    repair_user_message,
)
from llm_agents.deep_agents.planning.store import PlannedStep

logger = logging.getLogger("deep_agents.planner")

_TEMPLATE_ARG_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def template_args(data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """The run's template args from its structured input ``data`` — the
    scope pick (``{secType, code | industry, name?}``) and the question,
    flattened to the ``{{key}}`` vocabulary (``sec_type`` / ``code`` /
    ``name`` / ``question`` — empty when absent; ``code`` carries the
    industry id on an industry pick, mirroring
    text.deep_agent_identities)."""
    scope = (data or {}).get("scope")
    scope = scope if isinstance(scope, dict) else {}
    question = (data or {}).get("question")
    return {
        "sec_type": str(scope.get("secType") or "").strip(),
        "code": str(scope.get("code") or scope.get("industry") or "").strip(),
        "name": str(scope.get("name") or "").strip(),
        "question": str(question or "").strip(),
    }


def render_template(text: str, args: Dict[str, str]) -> str:
    """Substitute ``{{key}}`` placeholders: known keys -> the arg's value
    (empty string when the run carries none), unknown keys untouched."""
    return _TEMPLATE_ARG_RE.sub(
        lambda m: args[m.group(1)] if m.group(1) in args else m.group(0),
        text or "",
    )


def business_framing(data: Optional[Dict[str, Any]]) -> str:
    """The plan node's compact BUSINESS framing of the run's input —
    what the ask is ABOUT, rendered from the structured context so the
    planner's steps name the actual subjects and metrics instead of
    generic tags: the scope subject, the focus question, the selected
    skills/plan prompt keys, and the analysis items (label + metric
    count). Empty string when the input carries none of it (the raw
    JSON block still rides the message)."""
    data = data or {}
    lines: List[str] = []
    scope = data.get("scope")
    if isinstance(scope, dict):
        bits = [str(scope.get("secType") or ""),
                str(scope.get("code") or scope.get("industry") or "")]
        name = str(scope.get("name") or "").strip()
        if name:
            bits.append(f"“{name}”")
        subject = " ".join(b for b in bits if b)
        if subject:
            lines.append(f"Scope subject (every step works on it): {subject}")
    question = str(data.get("question") or "").strip()
    if question:
        lines.append(f"Focus question (the user's ask): {question}")
    prompts = [str(p) for p in (data.get("prompts") or []) if str(p)]
    if prompts:
        lines.append("Selected skills/plan prompts (fetch their texts from "
                     f"text.ai_prompts): {', '.join(prompts)}")
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


def plan_user_content(
    goal: str,
    data: Optional[Dict[str, Any]],
    plan_instructions: List[Tuple[str, str]],
) -> str:
    """The plan node's user message: the goal, the BUSINESS framing of
    the structured context (then the raw JSON block), and the selected
    plan prompts' instruction texts."""
    parts = [goal]
    framing = business_framing(data)
    if framing:
        parts.append(f"[What this run is about — every step is named "
                     f"after it and works on it, never after a data "
                     f"operation]\n{framing}")
    if data:
        parts.append("[Structured context — JSON]\n```json\n"
                     f"{json.dumps(data, ensure_ascii=False, indent=2)}\n```")
    if plan_instructions:
        quoted = "\n\n".join(
            f"- {key}: {instruction}"
            for key, instruction in plan_instructions)
        parts.append("[Plan prompts the user selected — honor their "
                     f"approach when laying out the steps]\n{quoted}")
    return "\n\n".join(parts)


class Planner:
    """One plan-proposal session: propose → validate → correct, until
    the model's reply compiles into accepted thread steps.

    The session's CONVERSATION is the object's state (``messages`` —
    every rejected reply stays in it as an assistant turn followed by
    the repair instruction, so each retry sees the full correction
    history) and ``calls`` counts the LLM calls it spent (the plan
    node's context metric, valid even when the session ends rejected).

    Built either from the run's business input (``goal`` / ``data`` /
    ``plan_instructions`` — the rendered ``(prompt_key, text)`` pairs
    of the selected plan prompts) or from an explicit ``messages`` list
    (the functional entry point's shape). ``chat_complete`` is
    duck-typed; ``repair_rounds`` corrections follow the first attempt.
    """

    def __init__(
        self,
        chat_complete: Any,
        *,
        goal: str = "",
        data: Optional[Dict[str, Any]] = None,
        plan_instructions: Optional[List[Tuple[str, str]]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        repair_rounds: int = 2,
        system_prompt: str = PLAN_SYSTEM_PROMPT,
    ) -> None:
        self._chat = chat_complete
        self._model = model
        self._temperature = temperature
        self._repair_rounds = max(0, int(repair_rounds))
        self.calls = 0
        self.messages: List[Dict[str, Any]] = (
            [dict(m) for m in messages] if messages is not None else [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": plan_user_content(
                    goal, data, list(plan_instructions or []))},
            ])

    async def propose(self) -> List[PlannedStep]:
        """Ask for the plan JSON and drive the correction round-trip
        until the reply validates: each rejected reply is appended to
        the session's messages (assistant turn + the repair
        instruction built from the validation complaint) and the model
        tries again — up to ``repair_rounds`` corrections after the
        first attempt. Returns the validated steps (the compile-able
        thread states); raises :class:`PlanJsonError` when every round
        is rejected, and propagates transport errors — the same
        degrade-to-no-plan contract either way."""
        for attempt in range(self._repair_rounds + 1):
            message = await self._chat(
                self.messages, model=self._model,
                temperature=self._temperature)
            self.calls += 1
            reply = str(message.get("content") or "")
            try:
                return parse_plan_json(reply)
            except PlanJsonError as exc:
                if attempt >= self._repair_rounds:
                    raise
                logger.info("plan JSON rejected (round %d/%d), asking the "
                            "model to correct it: %s",
                            attempt + 1, self._repair_rounds, exc)
                self.messages = self.messages + [
                    {"role": "assistant", "content": reply},
                    {"role": "user",
                     "content": repair_user_message(str(exc))},
                ]
        raise PlanJsonError("unreachable")  # the loop returns or raises


async def plan_reply_steps(
    chat_complete: Any,
    messages: List[Dict[str, Any]],
    *,
    model: Optional[str] = None,
    temperature: float = 0.2,
    repair_rounds: int = 2,
) -> List[PlannedStep]:
    """The functional form of :meth:`Planner.propose` over a pre-built
    message list — a thin wrapper (the class owns the loop); kept for
    the older call sites and tests."""
    planner = Planner(chat_complete, messages=messages, model=model,
                      temperature=temperature, repair_rounds=repair_rounds)
    return await planner.propose()
