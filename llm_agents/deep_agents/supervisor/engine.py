"""llm_agents.deep_agents.supervisor.engine — the SUPERVISOR WATCHDOG's
intervention engine: an INDEPENDENT agent beside the thread graph, not
a node in it, and NOT part of the langgraph package — it shares no
node, edge or data lane with the graph; only the runner's tail (the
langgraph composition point) invokes it.

Design (studied 2026-11-02 against the DeepSeek Super-agent write-up —
the blueprint skill's references):

  * PROACTIVE, STAYS QUIET — the Super-agent property "watches events,
    acts on what is worth handling, stays quiet otherwise": the
    supervisor is IDLE on healthy runs. It takes no graph data in or
    out routinely — no end-of-run review of findings/answer — so a
    healthy run pays ZERO supervisor LLM cost.
  * EVENTS, NOT PROMPTS — "code filters first, the model judges only
    what passes": the trigger is a PYTHON filter over the run's
    settled node states (any ROOT-PATH failure — a failed top-level
    step, or the failed planning row), never a prompt. The model wakes
    only for what passes.
  * WATCHDOG AS A SEPARATE SESSION — the supervisor is not a LangGraph
    node and shares no chain with the graph: the runner watches the
    node states from OUTSIDE (``runner.run_thread``'s watchdog tail)
    and calls this engine only on a root-path failure. Fork-path
    failures stay local (the parent gathers them), by the existing
    contract.
  * OBSERVE MODE — "unrequested work records what it WOULD have done":
    an intervention DIAGNOSES the error and judges whether the plan /
    graph topology needs an update, recording the recommendation as a
    supervisor-review context. It does NOT apply plan mutations and
    carries no veto — applying updates (a repair round behind
    PlanAuthority + HITL) is the roadmap's next step.
  * BOUNDED — ONE LLM call per intervention, fresh context (the
    watchdog's own — one node one context holds), fail-soft end to
    end: a failed intervention settles its item failed and changes
    nothing about the run's recorded result.

The intervention's INPUT is deliberately lean ("just node state, not
every graph data"): the run goal, the plan tree as title/status lines,
and for each failed root-path node its directive, its recorded error
and its tick note. The findings bridge, digests and transcripts stay
OUT. The OUTPUT is the diagnosis + verdict document, capped at
SUPERVISOR_REVIEW_CHARS and persisted by the runner (kind
supervisor-review, keyed to this call's chained context).

Pure module, stub-testable: the engine takes a ``chat_complete``
callable like the planner and compaction do.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from llm_agents.deep_agents.planning.store import StateRow
from llm_agents.deep_agents.supervisor.loop import BoundedToolLoop
from llm_agents.deep_agents.supervisor.repair import parse_action
from llm_agents.deep_agents.tools.base import BuiltinTool

# The supervisor's node-state namespace (the node:<name> item key the
# runner's watchdog tail writes its marker/review lifecycle under).
# Defined HERE — the supervisor owns its name; state_store imports it
# back (planning keeps its own mirror: planning must not import this
# package's langgraph modules either way).
NODE_SUPERVISOR = "supervisor"

# The review's hard char bound (the prompt asks for less; this is the
# belt under it — same backstop pattern as the compaction digest).
SUPERVISOR_REVIEW_CHARS = 4000


def root_path_failures(rows: List[StateRow]) -> List[StateRow]:
    """The TRIGGER FILTER (code, not model): the failed ROOT-PATH rows —
    top-level states (the main lane's steps AND the ordinal-0 planning
    row; fork children are excluded — their failure stays local to the
    parent that gathered them) whose status is failed. An error of
    'stopped by user' does not count (a stop is not a defect). Empty
    result = the supervisor stays IDLE."""
    return [
        s for s in rows
        if s.parent_state_id is None and s.status == "failed"
        and not str(s.error or "").startswith("stopped by user")
    ]


SUPERVISOR_SYSTEM_PROMPT = (
    "You are the SUPERVISOR of a planned multi-step research run — an "
    "independent watchdog BESIDE the run's graph, woken only because a "
    "ROOT-PATH node failed. You receive the run's goal, its plan tree "
    "as settled, and each failed node's directive, recorded error and "
    "tick note — nothing else, by design. Judge ONLY from what you are "
    "given; add NO facts of your own.\n"
    "Produce, plainly:\n"
    "Error: <what actually failed, per failed node — the error class "
    "in plain words (e.g. rate limit, step-cap exhaustion, tool/DB "
    "refusal, empty reply) and what it blocked>\n"
    "Plan/topology: <whether the plan or its shape is implicated — "
    "unchanged (the plan was sound; the failure was operational) | "
    "update-recommended — <the concrete change: split/merge steps, "
    "reorder, narrow a directive, move work to a fork, drop a step>>\n"
    "Overall: no-action | plan-update-recommended\n"
    "Action: retry-step | ignore-step | no-action — include this line "
    "ONLY when the message's repair protocol offers it: retry-step "
    "when the failure is operational and transient (a re-run can "
    "plausibly clear it — rate limit, timeout, upstream 5xx; a "
    "rate-limit retry is automatically throttled before the re-run); "
    "ignore-step when the failed step's work is dispensable or the "
    "fault is structural but non-fatal and the run should proceed "
    "past it; no-action otherwise (bad input, exhausted budget).\n"
    "Concise, specific, under ~2500 characters, the labeled lines "
    "first."
)


def _tree_block(rows: List[StateRow]) -> str:
    """The plan tree as the watchdog sees it — one title/status line per
    top-level node (forks collapsed to a count on their parent)."""
    if not rows:
        return "(no persisted plan — the planning phase itself failed)"
    return "\n".join(
        f"{s.ordinal}. [{s.node}] {s.title} — {s.status}"
        + (f" ({s.error})" if s.error else "")
        for s in rows
    )


def _failure_block(row: StateRow) -> str:
    parts = [f"[{row.ordinal}. {row.node}] {row.title}",
             f"status: {row.status}",
             f"error: {row.error or '(none recorded)'}"]
    return "\n".join(parts)


# The intervention message frame (2026-11-07 DB-editable) — seeded as the
# type-3 internal row ``deep-agent-supervisor-intervention``; the built-in
# constant is the fail-soft fallback. The runner-side data blocks (goal,
# plan tree, failed nodes) and the repair-eligibility conditionals fill
# the placeholders at render time.
INTERVENTION_FRAME = (
    "[Run goal — what the failed path was working toward]\n{{run_goal}}\n\n"
    "[Plan tree as it settled — every top-level node]\n{{plan_tree}}\n\n"
    "[Failed root-path node(s) — what you were woken for]\n{{failed_nodes}}\n\n"
    "{{repair_protocol}}\n\n"
    "[Your output]\n{{output_contract}}"
)


def intervention_user_message(
    goal: str,
    rows: List[StateRow],
    failures: List[StateRow],
    *,
    repair_eligible: bool = False,
    frame: Optional[str] = None,
) -> str:
    """The ONE intervention call's user message: run goal, the plan
    tree as settled, then each failed root-path node's own block. Lean
    by design — no findings bridge, no digests, no transcripts. When
    *repair_eligible* the message carries the REPAIR PROTOCOL block —
    the run is PAUSED in a bounded window and the Action line decides
    whether the failed step re-runs. ``frame`` is the fetched
    ``deep-agent-supervisor-intervention`` row (a ``{{placeholder}}``
    template over the same sections); None falls back to the built-in
    :data:`INTERVENTION_FRAME` (the seed and the constant must stay in
    sync). Sections whose placeholder rendered empty collapse out."""
    protocol = (
        "[Repair protocol]\n"
        "The run is PAUSED at the join waiting on you (bounded "
        "window; every parallel node before this barrier has "
        "settled): your Action line decides what resumes. Say "
        "retry-step for an operational, transient failure — a "
        "re-run plausibly clears it (rate limit, timeout, "
        "upstream 5xx, a transport hiccup; a rate-limit retry is "
        "throttled before the re-run). Say ignore-step when the "
        "step's work is dispensable — a failed reviewer, a "
        "nice-to-have study, a structural-but-non-fatal fault — "
        "the run proceeds past the node without it. Say "
        "no-action for bad input or exhausted budgets."
        if repair_eligible else "")
    output_contract = (
        "The labeled lines — Error:, Plan/topology:, Overall:"
        + (", Action: — nothing else." if repair_eligible
           else " — nothing else."))
    rendered = (
        (frame or INTERVENTION_FRAME)
        .replace("{{run_goal}}", goal)
        .replace("{{plan_tree}}", _tree_block(rows))
        .replace("{{failed_nodes}}",
                 "\n\n".join(_failure_block(f) for f in failures))
        .replace("{{repair_protocol}}", protocol)
        .replace("{{output_contract}}", output_contract))
    return "\n\n".join(
        section for section in rendered.split("\n\n") if section.strip())


class InterventionOutcome(BaseModel):
    """One intervention's parsed result: the bounded diagnosis text
    (what gets persisted as the supervisor-review) and the fail-soft
    Action verdict (``repair.parse_action`` semantics — a missing or
    unrecognized line reads ``no-action``)."""

    model_config = ConfigDict(frozen=True)

    text: str
    action: str = "no-action"


async def intervene_on_failure(
    chat_complete: Callable[..., Any],
    *,
    goal: str,
    rows: List[StateRow],
    failures: List[StateRow],
    model: Optional[str] = None,
    temperature: float = 0.1,
    system_prompt: Optional[str] = None,
    builtin_tools: Optional[Dict[str, BuiltinTool]] = None,
    tool_addendum: str = "",
    repair_eligible: bool = False,
    message_frame: Optional[str] = None,
) -> InterventionOutcome:
    """Run the watchdog's intervention: the failure context in, the
    bounded diagnosis + verdict out. WITHOUT ``builtin_tools`` this is
    the original ONE call (a healthy diagnostic needs no tools);
    WITH them the bounded :class:`loop.BoundedToolLoop` runs — the
    supervisor may re-check the failed node's recorded traffic
    (``search_thread_context``) or the DB semantics (``kg_search``)
    before judging. ``repair_eligible`` mounts the repair protocol
    (the paused-run contract whose Action line the poller gates).
    ``message_frame`` is the fetched ``deep-agent-supervisor-
    intervention`` row (None = the built-in frame). Raises on a
    provider failure or an empty reply — the watchdog's fail-soft path
    settles the item failed and the run's recorded result never
    changes."""
    system_text = system_prompt or SUPERVISOR_SYSTEM_PROMPT
    if builtin_tools and tool_addendum:
        system_text = f"{system_text}\n\n{tool_addendum}"
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_text},
        {"role": "user",
         "content": intervention_user_message(
             goal, rows, failures, repair_eligible=repair_eligible,
             frame=message_frame)},
    ]
    if builtin_tools:
        message = await BoundedToolLoop(
            chat_complete, builtin_tools,
            model=model, temperature=temperature).run(messages)
    else:
        message = await chat_complete(
            messages, model=model, tools=None, temperature=temperature)
    raw = str(message.get("content") or "")
    # The Action verdict parses from the RAW reply (the collapse below
    # flattens newlines — an anchored line match would never fire).
    action = parse_action(raw)
    text = " ".join(raw.split())
    if not text:
        raise ValueError("supervisor intervention reply was empty")
    if len(text) > SUPERVISOR_REVIEW_CHARS:
        text = text[:SUPERVISOR_REVIEW_CHARS - 1] + "…"
    return InterventionOutcome(text=text, action=action)
