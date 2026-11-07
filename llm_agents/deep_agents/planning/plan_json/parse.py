"""llm_agents.deep_agents.planning.plan_json.parse — the plan reply's
PARSE + VALIDATE half of the contract.

Fence/prose tolerant like every LLM JSON reply, strict on the
load-bearing bits (a JSON object with a non-empty ``steps`` list of
titled steps); every rejection carries a SPECIFIC, model-actionable
complaint. The plan is BUSINESS-shaped or rejected: node tags naming a
data operation (fetch_/load_/query_/...) and titles opening with one
are bounced with a rename ask — the plan says what each step settles
about the ask's subject, the executor owns the how. ``parse_update_
steps`` runs the SAME per-step validation for the runtime ``update_
plan`` tool's add_steps array (planned and runtime-added steps
validate identically), and ``parse_plan_reply`` adds the optional
``load_prompts`` verdict (the prompt-loading ladder's LLM rung) and
the ``more`` continuation flag (the batched proposal protocol).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.store import PlannedStep

from llm_agents.deep_agents.planning.plan_json.contract import (
    MAX_DELEGATE_LEN, MAX_NODE_LEN, MAX_TITLE_LEN, REVIEW_NODE_TAGS,
    PlanJsonError,
)

logger = logging.getLogger("deep_agents.plan_json")

# The ops-name detector: a plan step is a business question about the
# subject, not a data operation. Tags/titles wearing these HOW-words
# (fetching, loading, querying, computing...) are rejected with a
# rename ask — the correction round then teaches the model the
# business phrasing instead of the run silently executing an
# ops-shaped plan.
_OPS_NODE_PREFIXES = (
    "fetch", "load", "query", "get", "read", "pull", "write", "insert",
    "upsert", "delete", "remove", "update", "call", "invoke", "run",
    "exec", "execute", "setup", "init", "connect", "verify", "check",
    "validate", "compute", "calc", "scrape", "download", "upload",
    "persist", "save", "select", "join", "aggregate", "spawn",
)
# Titles get the lighter touch: business English may open with check/
# study/examine, so only the data-plumbing verbs lead to a rejection.
_OPS_TITLE_START_RE = re.compile(
    r"^(?:fetch|load|query|pull|read|write|insert|upsert|delete|remove|"
    r"update|call|invoke|execute|exec|setup|initialize|initialise|connect|"
    r"verify|validate|compute|calculate|scrape|download|upload|persist|"
    r"save|select|get)\b",
    re.IGNORECASE,
)


def _ops_complaints(steps: List["PlannedStep"]) -> str:
    """The model-actionable complaint for ops-named steps — '' when
    every step is business-named. Node tags are checked against the
    prefix blacklist (the graph canvas shows the tag as the step's
    name, so it must be an aspect of the ask); titles only when they
    OPEN with a data-plumbing verb (a question phrased around the
    ask's metric is wanted, an imperative to fetch data is not)."""
    bad_nodes = [s.node for s in steps
                 if any(s.node.strip().lower() == prefix
                        or s.node.strip().lower().startswith(prefix + "_")
                        or s.node.strip().lower().startswith(prefix + "-")
                        for prefix in _OPS_NODE_PREFIXES)]
    title_offenders = [
        f"\"{s.title[:60]}\"" for s in steps
        if _OPS_TITLE_START_RE.match(s.title.strip())]
    complaints: List[str] = []
    if bad_nodes:
        complaints.append(
            "node tag(s) " + ", ".join(f"'{n}'" for n in bad_nodes)
            + " are operation names — name each node after the BUSINESS "
            "aspect of the ask it settles (e.g. moving_average_state, "
            "momentum_state, volume_price_behavior); data plumbing "
            "(fetching/loading/querying) is the executor's business, "
            "not a plan step")
    if title_offenders:
        complaints.append(
            "title(s) " + ", ".join(title_offenders)
            + " open with a data operation — restate each as the "
            "business question it settles (e.g. \"Is 000300 holding "
            "above its 20/60-day moving averages?\")")
    return "; ".join(complaints)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def _step_delegate(raw: Dict[str, Any]) -> Optional[str]:
    """One raw step's delegation goal — accepts ``{"delegate":
    {"goal": ...}}``, a plain string ``"delegate"``, or the flat
    ``"delegate_goal"`` key (all three appear across model styles).
    None = an inline step; an empty/oversized goal degrades to None
    (a fork without a usable goal is just an ordinary step)."""
    candidate = raw.get("delegate", raw.get("delegate_goal"))
    if isinstance(candidate, dict):
        candidate = candidate.get("goal")
    if not isinstance(candidate, str):
        return None
    goal = candidate.strip()
    if not goal:
        return None
    return goal[:MAX_DELEGATE_LEN]


def _parallel_mark(raw: Dict[str, Any]) -> bool:
    """One raw step's independence mark: true only when the model set
    \"parallel\": true."""
    return raw.get("parallel") is True


def _review_fixup(step: PlannedStep) -> PlannedStep:
    """The REVIEW WAVE's structural normalization, applied at every
    parse site: a review-tagged step is an INDEPENDENT inline reviewer -
    parallel forced on (it joins the closing wave whatever the model
    marked) and any delegate goal stripped BEFORE the delegated cap
    counts it (a delegated fork never sees the findings bridge, so a
    fork cannot review; the callers apply this before the cap logic)."""
    if step.node not in REVIEW_NODE_TAGS:
        return step
    if step.delegate is None and step.parallel:
        return step
    return step.model_copy(update={'delegate': None, 'parallel': True})


class PlanReply(BaseModel):
    """One parsed plan reply (2026-11-06 — the former ``(steps, verdict,
    more)`` tuple): the validated steps, the optional ``load_prompts``
    verdict (the prompt-loading ladder's LLM rung) and the ``more``
    continuation flag of the batched proposal protocol."""

    steps: List[PlannedStep]
    verdict: Optional[List[str]] = None
    more: bool = False


def parse_plan_reply(text: str) -> PlanReply:
    """Parse the model's plan reply into (validated steps, the optional
    ``load_prompts`` verdict, the ``more`` continuation flag). The
    steps side is EXACTLY :func:`parse_plan_json` (same tolerance, same
    complaints); the verdict is the gradual prompt-loading ladder's LLM
    rung — the keys of the cataloged skills the plan actually works
    under (None when the reply carries no usable ``load_prompts``
    array; unknown or duplicated keys dropped, order preserved);
    ``more`` is True only when the reply explicitly sets it — a batch
    protocol reply promising MORE steps after this one (the batched
    proposal session keeps asking while ``more`` holds)."""
    steps = parse_plan_json(text)
    verdict: Optional[List[str]] = None
    more = False
    stripped = _FENCE_RE.sub("", (text or "").strip()).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(stripped[start:end + 1])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            raw = payload.get("load_prompts")
            if isinstance(raw, list):
                seen: List[str] = []
                for key in raw:
                    cleaned = str(key).strip()
                    if cleaned and cleaned not in seen:
                        seen.append(cleaned)
                verdict = seen
            more = payload.get("more") is True
    return PlanReply(steps=steps, verdict=verdict, more=more)


def parse_plan_json(text: str) -> List[PlannedStep]:
    """Parse the model's plan reply into validated steps.

    Tolerates markdown fences and prose around the JSON object, extra
    steps beyond the configured max_plan_nodes (truncated) and untitled
    steps among usable ones (dropped). Delegation markers past the
    configured max_delegated_steps are dropped too (the step stays,
    inline). Raises
    :class:`PlanJsonError` with a model-actionable message for every
    format it cannot accept.
    """
    stripped = _FENCE_RE.sub("", (text or "").strip()).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise PlanJsonError(
            "reply has no JSON object — reply with ONLY the plan JSON "
            '{"steps": [...]}, no prose around it')
    try:
        payload = json.loads(stripped[start:end + 1])
    except json.JSONDecodeError as exc:
        raise PlanJsonError(
            f"invalid JSON ({exc.msg} at line {exc.lineno} column "
            f"{exc.colno}) — reply with ONLY the corrected plan JSON "
            "object") from exc
    if not isinstance(payload, dict):
        raise PlanJsonError(
            "top-level JSON must be an object with a 'steps' key")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanJsonError(
            "the plan JSON object needs a non-empty 'steps' LIST of "
            '{"node", "title", "notes"} objects')

    steps: List[PlannedStep] = []
    dropped = 0
    delegated = 0
    limits = run_limits()
    for raw in raw_steps[:limits.max_plan_nodes]:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            dropped += 1
            continue
        notes = raw.get("notes")
        if notes is not None and not isinstance(notes, (str, dict)):
            notes = str(notes)
        parallel = _parallel_mark(raw)
        node = str(raw.get("node") or "").strip() or f"step_{len(steps) + 1}"
        step = PlannedStep(
            node=node[:MAX_NODE_LEN],
            title=title[:MAX_TITLE_LEN],
            notes=notes,
            delegate=_step_delegate(raw),
            parallel=parallel,
        )
        # The review wave normalizes FIRST (delegate stripped before the
        # cap logic counts it - a review step is always inline+parallel).
        step = _review_fixup(step)
        if step.delegate is not None:
            delegated += 1
            if delegated > limits.max_delegated_steps:
                logger.info("plan JSON: delegate marker dropped on %d-th "
                            "fork (cap %d)", delegated,
                            limits.max_delegated_steps)
                step = step.model_copy(update={'delegate': None})
        steps.append(step)
    if not steps:
        raise PlanJsonError(
            "no usable steps — every entry of 'steps' must be an object "
            "with a non-empty 'title' string")
    ops = _ops_complaints(steps)
    if ops:
        raise PlanJsonError(ops)
    if dropped:
        logger.info("plan JSON: dropped %d untitled/malformed step(s)", dropped)
    return steps


def parse_update_steps(raw_steps: Any) -> Tuple[List[PlannedStep], str]:
    """Parse + validate the ``add_steps`` array of an update_plan call —
    the SAME per-step contract as the plan JSON (node / title / notes /
    delegate), reused so runtime-added steps validate identically to
    planned ones. Returns (steps, error) with exactly one populated:
    a model-actionable error message when the array is unusable."""
    if not isinstance(raw_steps, list) or not raw_steps:
        return [], "'add_steps' must be a non-empty list of step objects"
    steps: List[PlannedStep] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            return [], "every add_steps entry must be an object"
        title = str(raw.get("title") or "").strip()
        if not title:
            return [], "every add_steps entry needs a non-empty 'title'"
        notes = raw.get("notes")
        if notes is not None and not isinstance(notes, (str, dict)):
            notes = str(notes)
        node = str(raw.get("node") or "").strip() or f"step_{len(steps) + 1}"
        steps.append(_review_fixup(PlannedStep(
            node=node[:MAX_NODE_LEN], title=title[:MAX_TITLE_LEN],
            notes=notes, delegate=_step_delegate(raw),
            parallel=_parallel_mark(raw))))
    ops = _ops_complaints(steps)
    if ops:
        return [], f"update_plan error: {ops}"
    return steps, ""
