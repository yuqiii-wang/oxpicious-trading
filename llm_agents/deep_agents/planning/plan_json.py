"""llm_agents.deep_agents.planning.plan_json — the plan-JSON contract between the
LLM and the thread runner.

The deep-agent run's plan node — the HARDCODED first node of every run —
asks the model to translate its plan into the states JSON the runner
compiles the thread from:

    {"steps": [{"node": "trend_structure", "title": "...", "notes": "...",
                "delegate": {"goal": "..."}}]}

one step becoming one pending ``text.deep_agent_states`` row (the
LangGraph task states the execute node ticks). A step carrying a
``delegate`` goal is a FORK: the execute node's agent runs it by
spawning a background subagent with that goal — the forked path the
thread tree grows (``plan_store.parent_state_id``). This module owns
that contract end to end:

  * the system prompt that states the JSON shape (PLAN_SYSTEM_PROMPT);
  * parse + validate (``parse_plan_json``) — fence/prose tolerant like
    every LLM JSON reply, strict on the load-bearing bits (a JSON object
    with a non-empty ``steps`` list of titled steps); every rejection
    carries a SPECIFIC, model-actionable complaint. The plan is
    BUSINESS-shaped or rejected: node tags naming a data operation
    (fetch_/load_/query_/...) and titles opening with one are bounced
    with a rename ask — the plan says what each step settles about the
    ask's subject, the executor owns the how;
  * ``parse_update_steps`` — the same per-step validation for the
    runtime ``update_plan`` tool's add_steps array (planned and
    runtime-added steps validate identically);
  * the repair instruction builder (``repair_user_message``) — the
    correction turn the proposal session feeds back to the model.

The PROPOSAL SESSION itself — the propose → validate → correct loop
that keeps asking the model until the reply compiles into accepted
thread states (or the rounds run out and the runner degrades to
no-plan) — is the independent :class:`Planner` class in
``planning.planner`` (this module stays pure contract: prompt text,
parse, validate; the runner wraps the whole thing fail-soft as
always).

Pure module: no DB, no provider import (the provider rides in duck-
typed through the Planner), so the validation and the correction loop
unit-test against a stub.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from llm_agents.deep_agents.planning.store import PlannedStep

logger = logging.getLogger("deep_agents.plan_json")

# Plan guardrails: a plan is a checklist, not the work itself.
MAX_PLAN_STEPS = 12
MAX_TITLE_LEN = 300
MAX_NODE_LEN = 60
# A delegation goal must stay a goal, not a second plan document.
MAX_DELEGATE_LEN = 1500
# Forks fan out into parallel subagents — a plan of all-forks is a
# scatter, so cap how many steps may carry a delegate marker.
MAX_DELEGATED_STEPS = 6

PLAN_SYSTEM_PROMPT = (
    "You are the planning node of a financial research agent with "
    "read-only database tools. Lay out a SHORT ordered plan of BUSINESS "
    "steps for the goal below. A plan step says WHAT it settles about "
    "the subject — never HOW data is fetched: the executor picks the "
    "tables and writes the queries, so data operations are not steps. "
    "NAME EVERY STEP FROM THE ASK: the ask's own words (its focus "
    "question, its selected skills/plan prompts, its analysis items) "
    "supply the step names — asked to study moving averages, a step is "
    "about the moving-average state of that code; asked about "
    "RSI/Bollinger/momentum/options positioning, those words name the "
    "steps. When a scope pick rides with the goal, EVERY step works on "
    "that one subject. Reply with ONLY a JSON object, no prose:\n"
    '{"steps": [{"node": "trend_structure", "title": "...", "notes": '
    '"...", "delegate": {"goal": "..."}}]}\n'
    "- node: the step's NAME ON THE GRAPH — a short snake_case tag "
    "naming the BUSINESS aspect of the ASK this step settles, built "
    "from the ask's own vocabulary (moving_average_state, "
    "rsi_momentum, bollinger_position, ma_spread_trend, "
    "valuation_level, volume_price_behavior, signal_consensus, "
    "options_wall_positioning, margin_flows, risk_factors, "
    "final_view, ...). NEVER an operation verb — fetch_/load_/query_/"
    "get_/read_/check_/verify_/compute_/write_ and the like are "
    "REJECTED: the tag says what the step is ABOUT, not what it does\n"
    "- title: ONE concrete business question or claim about the "
    "subject this step settles, in the ask's own terms, <= 160 chars "
    "(e.g. \"Is 000300 holding above its 20/60-day moving averages, "
    "and what does the spacing say about the trend?\")\n"
    "- notes (optional): the business evidence it must settle — the "
    "claims, metrics or signal readings it weighs\n"
    "- delegate (optional): mark the step as a FORK — a self-contained "
    "chunk a separate subagent pursues (e.g. one deep-dive per skill "
    "or per scope subject whose intermediate queries would flood the "
    "main thread). CONSECUTIVE delegated steps run as ONE parallel "
    "wave (a fork goal is fixed before any step runs, so sibling "
    "forks cannot depend on each other): give each fork a "
    "NON-OVERLAPPING question — one subject or one aspect per fork — "
    "and keep any step that must weigh earlier findings INLINE "
    "between the waves. Its \"goal\" must OPEN with the business "
    "question (the subject + what to settle) and then carry the "
    "COMPLETE self-contained instruction for that subagent: the "
    "tables/columns to check and what to report back — the subagent "
    "automatically also receives the run's business context (subject, "
    "focus question, selected prompts), so the goal states THIS "
    "fork's question, not the run's. Do NOT delegate the final "
    "synthesis step or anything that needs the run's whole context. "
    f"Use at most {MAX_DELEGATED_STEPS} delegated steps.\n"
    "- parallel (optional, default false): mark a step whose work does "
    "NOT need any earlier step's findings — an independent aspect study "
    "(e.g. the moving-average state vs the volatility state of the same "
    "subject are mutually independent; either settles without the "
    "other). CONSECUTIVE parallel and/or delegated steps execute as ONE "
    "concurrent wave, so keep every independent study of the batch "
    "adjacent and marked, and leave steps that must weigh earlier "
    "findings (the final synthesis) unmarked so they run in order after "
    "the wave. Never mark the synthesis step parallel.\n"
    "- steps are RESEARCH work only — never plan a step about calling "
    "update_plan_step or reporting progress; ticking is automatic "
    "bookkeeping. Plans are living documents: the executor may extend "
    "or rework not-yet-started steps later, so order steps so the "
    "early ones de-risk the later ones.\n"
    f"Use at most {MAX_PLAN_STEPS} steps. The run's plan and result "
    "nodes are automatic — plan only the business steps between them, "
    "ending with the synthesis step (node final_view) that weighs the "
    "earlier steps' findings into the concluding view of the subject "
    "(its answer becomes the run's result document). No steps that "
    "only restate the goal."
)

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


class PlanJsonError(ValueError):
    """The model's plan reply violates the plan-JSON contract — the
    message is the specific complaint the repair round feeds back."""


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


# Nodes that are ORDERED by definition — the synthesis always weighs
# every earlier finding, so a parallel mark on it (hallucinated or
# copied from a sibling) is dropped at parse time.
ORDERED_NODES = frozenset({"final_view"})


def _parallel_mark(raw: Dict[str, Any]) -> bool:
    """One raw step's independence mark: true only when the model set
    \"parallel\": true AND the node is not ordered-by-definition."""
    return (raw.get("parallel") is True
            and str(raw.get("node") or "").strip() not in ORDERED_NODES)


def parse_plan_json(text: str) -> List[PlannedStep]:
    """Parse the model's plan reply into validated steps.

    Tolerates markdown fences and prose around the JSON object, extra
    steps beyond MAX_PLAN_STEPS (truncated) and untitled steps among
    usable ones (dropped). Delegation markers past MAX_DELEGATED_STEPS
    are dropped too (the step stays, inline). Raises
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
    for raw in raw_steps[:MAX_PLAN_STEPS]:
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
        delegate = _step_delegate(raw)
        if delegate is not None:
            delegated += 1
            if delegated > MAX_DELEGATED_STEPS:
                logger.info("plan JSON: delegate marker dropped on %d-th "
                            "fork (cap %d)", delegated, MAX_DELEGATED_STEPS)
                delegate = None
        # The synthesis step is ordered by definition (it weighs every
        # earlier finding) — its parallel mark, if any, is ignored.
        parallel = _parallel_mark(raw)
        node = str(raw.get("node") or "").strip() or f"step_{len(steps) + 1}"
        steps.append(PlannedStep(
            node=node[:MAX_NODE_LEN],
            title=title[:MAX_TITLE_LEN],
            notes=notes,
            delegate=delegate,
            parallel=parallel,
        ))
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
        steps.append(PlannedStep(
            node=node[:MAX_NODE_LEN], title=title[:MAX_TITLE_LEN],
            notes=notes, delegate=_step_delegate(raw),
            parallel=_parallel_mark(raw)))
    ops = _ops_complaints(steps)
    if ops:
        return [], f"update_plan error: {ops}"
    return steps, ""


def repair_user_message(error: str) -> str:
    """The correction-round user message: the specific validation
    complaint plus the format restated, so the model fixes its own
    reply instead of guessing."""
    return (
        "Your previous reply is not a valid plan JSON — "
        f"{error}\n"
        "Reply again with ONLY the corrected JSON object "
        '{"steps": [{"node": "...", "title": "...", "notes": "..."}]} '
        "— no prose, no markdown fences."
    )
