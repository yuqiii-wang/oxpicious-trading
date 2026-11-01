"""llm_agents.deep_agents.planning.authorities — the plan-mutation permission model.

The thread's plan is a LIVING graph: beside ``update_plan_step``
(ticking a step's lifecycle as before), every agent on the run — the
MAIN thread and each forked subagent — carries tools to UPDATE the
plan when its work hits something that needs further investigation.
Who may touch what is NOT model-decided: it is owned by this module's
:class:`PlanAuthority`, minted per conversation by the runner:

  MAIN authority (each step node's conversation in the step graph —
  the thread's main lane) — the thread as a whole is its scope:
    * the step's own lifecycle is RUNNER-side (the node ticks its
      state row in_progress/completed/failed — ticking is automatic
      bookkeeping, never a model tool on the main lane);
    * ``update_plan`` — add steps (optionally delegated forks),
      revise or remove NOT-YET-RUN steps only, and TERMINATE running
      background forks — "the LLM on the main thread can update the
      graph for nodes not yet run and terminate running sub agents".

  SUB authority (one per spawned subagent, bound to its fork state) —
  its forked path is its ONLY scope:
    * tick ONLY the states it minted itself, addressed by the state id
      the add returned;
    * add investigation nodes UNDER its own fork state (worked by the
      subagent itself — a fork node has no delegation marker, forking
      further happens through the spawn tool instead);
    * below the spawn-depth cap the subagent also carries
      spawn_subagent: a sub-sub agent's child state lands under ITS
      fork state the same way (registered by its own hooks), so its
      forked path may nest one level deeper;
    * no revise, no remove, no cancel — a subagent can never touch
      another node or agent's work ("sub agents can only update nodes
      after the forked node paths not affecting other nodes and
      agents").

Structural enforcement, not prompt enforcement: the sub handler's
writes are keyed to ``fork_state_id`` and the minted-id set, so a
hallucinated ordinal/state id in a subagent call is rejected by the
handler, and plan_store's pending-guards reject writes to started
rows. DB failures surface as tool-result text (the loop must keep
walking); the rows settle at finalize either way.

The EXTERNAL authority (:func:`external_add_steps`) is a third writer:
the daemon's POST /threads/:id/steps endpoint — an out-of-band caller
(operator tooling, another service) extending a RUNNING thread's main
lane. It is add-only and top-lane-only: settled rows are HISTORY and
never move (add_plan_states shifts later rows' ordinals, never a
status or a context pointer), pending rows on every path — the main
lane's future steps and each fork's subtree — are kept (children are
untouched: the shift filters parent_state_id IS NULL), and the same
vocabulary/caps as the model's own adds apply (business node names,
MAX_ADDS_PER_CALL, MAX_TOTAL_STEPS). The running graph picks the new
nodes up on its next hop — the router re-reads the living tree between
hops, so an external topology change lands exactly like a model
replan.

The rendered plan (``render_plan`` / ``render_added``) is the model's
source of truth after every mutation — the preamble shows the plan as
laid out, and each update tool's RESULT carries the current rendered
tree, so a replan never depends on the model remembering its own edit.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from llm_agents.deep_agents.core.tools import BuiltinTool
from llm_agents.deep_agents.planning.thread_search import thread_search_tool
from llm_agents.deep_agents.planning.plan_json import (    MAX_TITLE_LEN, parse_update_steps,
)
from llm_agents.deep_agents.planning.store import (
    PLAN_NODE, PlannedStep, StateRow, add_plan_states, fetch_state,
    list_thread_states, revise_pending_state, skip_pending_states, tick_step,
)

logger = logging.getLogger("deep_agents.plan_tools")


async def _main_lane_states(thread_id: str) -> List[StateRow]:
    """The main lane AS THE MODEL SEES IT: the top-level rows minus the
    runner's own planning-node row (begin_thread's ordinal-0 'plan' row
    — run bookkeeping, not a planned step; the model must neither read
    nor address it)."""
    return [state for state in await list_thread_states(thread_id)
            if state.node != PLAN_NODE]

# Runtime guardrails: replanning must not outgrow the run.
MAX_TOTAL_STEPS = 24
MAX_FORK_CHILDREN = 8
MAX_ADDS_PER_CALL = 6

# The tick lifecycle the handlers allow: pending -> in_progress ->
# completed/failed (a completed-without-start tick is tolerated — the
# model may finish a trivial step in one breath).
TICKABLE_FROM: Dict[str, Set[str]] = {
    "pending": {"in_progress", "completed", "failed"},
    "in_progress": {"completed", "failed"},
}


# ----------------------------------------------------------------------
# Schemas — one tick tool and one update tool, each in a main and a sub
# variant (same names, authority-narrowed parameters: the sub schemas
# simply omit what a fork may not do, so the model never sees an option
# it must not use).
# ----------------------------------------------------------------------

UPDATE_PLAN_STEP_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "update_plan_step",
        "description": (
            "Update one plan step's progress. Call it with status "
            "'in_progress' when you START a step, and with 'completed' "
            "or 'failed' (plus a one-line note) when the step's work is "
            "done — exactly one step in_progress at a time, and every "
            "step ticked before your final answer."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ordinal": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "The plan step's 1-based ordinal.",
                },
                "status": {
                    "type": "string",
                    "enum": ["in_progress", "completed", "failed"],
                    "description": "The step's new status.",
                },
                "note": {
                    "type": "string",
                    "description": "One line recording what the step "
                                   "confirmed (pass/fail + the numbers) "
                                   "or why it failed.",
                },
            },
            "required": ["ordinal", "status"],
        },
    },
}

SUB_UPDATE_PLAN_STEP_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "update_plan_step",
        "description": (
            "Tick one of YOUR OWN fork's investigation nodes by the "
            "state id its update_plan call reported: 'in_progress' when "
            "you start it, 'completed'/'failed' (plus a one-line note) "
            "when done. You cannot tick any other node."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "state_id": {
                    "type": "integer",
                    "description": "The state id your update_plan "
                                   "add reported for the node.",
                },
                "status": {
                    "type": "string",
                    "enum": ["in_progress", "completed", "failed"],
                    "description": "The node's new status.",
                },
                "note": {
                    "type": "string",
                    "description": "One line recording what the node "
                                   "confirmed or why it failed.",
                },
            },
            "required": ["state_id", "status"],
        },
    },
}

UPDATE_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "update_plan",
        "description": (
            "Rework the plan's NOT-YET-STARTED part when your findings "
            "change what remains to be done: add steps (optionally "
            "delegated forks with a self-contained goal), revise or "
            "remove pending steps, and terminate running background "
            "subagents when the plan changed fundamentally. Steps "
            "already in_progress/completed/failed are history — they "
            "never move. The result carries the current rendered plan."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "add_steps": {
                    "type": "array",
                    "maxItems": MAX_ADDS_PER_CALL,
                    "description": "Steps to insert, in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "node": {
                                "type": "string",
                                "description": "Short snake_case tag "
                                               "naming the BUSINESS "
                                               "aspect of the ask this "
                                               "step settles (e.g. "
                                               "moving_average_state) "
                                               "— never an operation "
                                               "(fetch_/load_/query_ "
                                               "are rejected).",
                            },
                            "title": {"type": "string"},
                            "notes": {"type": "string"},
                            "delegate_goal": {
                                "type": "string",
                                "description": "Mark the step as a fork: "
                                               "the COMPLETE self-"
                                               "contained goal a "
                                               "background subagent "
                                               "will pursue — open "
                                               "with the business "
                                               "question (subject + "
                                               "what to settle); the "
                                               "subagent also receives "
                                               "the run's business "
                                               "context (subject, "
                                               "focus question, "
                                               "selected prompts) "
                                               "automatically.",
                            },
                            "parallel": {
                                "type": "boolean",
                                "description": "Mark the step "
                                               "independent of every "
                                               "earlier step's "
                                               "findings: consecutive "
                                               "parallel/delegated "
                                               "steps run as ONE "
                                               "concurrent wave. Never "
                                               "on a step that must "
                                               "weigh earlier findings.",
                            },
                        },
                        "required": ["title"],
                    },
                },
                "after_ordinal": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Insert add_steps AFTER this step's "
                                   "ordinal; omit to append at the "
                                   "plan's end.",
                },
                "revise": {
                    "type": "object",
                    "description": "Rewrite one PENDING step.",
                    "properties": {
                        "ordinal": {"type": "integer"},
                        "title": {"type": "string"},
                        "notes": {"type": "string"},
                        "delegate_goal": {
                            "type": "string",
                            "description": "Set/replace the fork goal; "
                                           "'' clears the fork marker.",
                        },
                    },
                    "required": ["ordinal"],
                },
                "remove_ordinals": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "PENDING steps to drop (settled "
                                   "skipped, removal recorded).",
                },
                "cancel_run_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Running background subagent run "
                                   "ids to terminate now.",
                },
                "cancel_running": {
                    "type": "boolean",
                    "description": "Terminate EVERY running background "
                                   "subagent (a total replan).",
                },
            },
        },
    },
}

SUB_UPDATE_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "update_plan",
        "description": (
            "Extend YOUR OWN forked path when your work hits something "
            "that needs further investigation: add_nodes appends "
            "investigation nodes under your fork (you then work and "
            "tick them yourself with update_plan_step). You cannot "
            "touch any other node, step, or agent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "add_nodes": {
                    "type": "array",
                    "maxItems": MAX_ADDS_PER_CALL,
                    "description": "Investigation nodes to add under "
                                   "your fork, in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "node": {
                                "type": "string",
                                "description": "Short snake_case tag "
                                               "naming the BUSINESS "
                                               "aspect this node "
                                               "settles (e.g. "
                                               "moving_average_state) "
                                               "— never an operation "
                                               "(fetch_/load_/query_ "
                                               "are rejected).",
                            },
                            "title": {"type": "string"},
                            "notes": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                },
            },
            "required": ["add_nodes"],
        },
    },
}


# ----------------------------------------------------------------------
# Rendering — the model's source of truth after every mutation
# ----------------------------------------------------------------------

def render_plan(states: List[StateRow]) -> str:
    """The MAIN lane as the model reads it: ordinal, status, node, title
    (children of forks are deliberately absent — their lanes belong to
    the subagents that own them)."""
    if not states:
        return "(the plan has no steps)"
    return "\n".join(
        f"{s.ordinal}. [{s.status}] {s.node} — {s.title}" for s in states)


def render_added(states: List[StateRow]) -> str:
    """Newly minted nodes as their add result echoes them (state ids the
    subagent addresses its ticks by)."""
    return "\n".join(
        f"- state {s.state_id} [{s.status}] {s.node} — {s.title}"
        for s in states)


# ----------------------------------------------------------------------
# The authority
# ----------------------------------------------------------------------

@dataclass
class PlanAuthority:
    """One conversation's write scope over the thread's plan graph.

    ``main`` authorities own the pending top-level lane plus fork
    termination; ``sub`` authorities own exactly the nodes they minted
    under their fork state. The runner mints one per conversation and
    closes the handlers over it — the model never sees a scope choice."""

    thread_id: str
    # The MAIN-lane in-progress tracker the runner's spawn parenting
    # reads (shared mutable cell — [current_state_id]). The LAST-TICK
    # cell beside it is the parenting FALLBACK: a fork spawned in the
    # same step as its tick (sibling calls race the tracker) parents to
    # the step the tick just touched instead of polluting the top lane.
    current_state_id: Optional[List[Optional[int]]] = None
    last_state_id: Optional[List[Optional[int]]] = None
    # MAIN only: terminate background forks — the runner binds the
    # execute agent's canceller here after the agent exists (None =
    # every running fork; each id also settles its fork state). Async
    # or sync callable; returns the ids that actually stopped.
    cancel_runs: Optional[Callable[[Optional[List[str]]],
                                   Union[List[str], Any]]] = None
    # SUB only: the fork state every add parents to, the context the
    # nodes run in, and the ids this authority has minted (its tick
    # scope — nothing else in the tree is addressable).
    fork_state_id: Optional[int] = None
    llm_context_id: Optional[str] = None
    minted: Set[int] = field(default_factory=set)

    @property
    def is_main(self) -> bool:
        return self.fork_state_id is None


def _tick_transition(state: StateRow, status: str) -> Optional[str]:
    """The lifecycle guard: None = allowed, else the model-facing
    complaint (a settled or regressive tick is rejected, not written)."""
    allowed = TICKABLE_FROM.get(state.status)
    if allowed is None:
        return (f"step is already settled ({state.status}) — settled "
                "steps never move")
    if status not in allowed:
        return (f"a {state.status} step cannot move to {status} "
                "(lifecycle is pending -> in_progress -> "
                "completed/failed)")
    return None


def _parse_add_steps(arguments: Dict[str, Any],
                     key: str = "add_steps") -> Tuple[List[PlannedStep], str]:
    raw = arguments.get(key)
    if raw is None:
        return [], ""
    steps, error = parse_update_steps(raw)
    if error:
        return [], f"update_plan error: {error}"
    if len(steps) > MAX_ADDS_PER_CALL:
        return [], (f"update_plan error: at most {MAX_ADDS_PER_CALL} "
                    "steps per call")
    return steps, ""


async def _rendered(authority: PlanAuthority) -> str:
    if not authority.is_main:
        # The sub's lane is its minted nodes only.
        nodes: List[StateRow] = []
        for state_id in sorted(authority.minted):
            state = await fetch_state(state_id)
            if state is not None:
                nodes.append(state)
        return render_added(nodes) if nodes else "(no nodes under your fork)"
    return render_plan(await _main_lane_states(authority.thread_id))


def tick_handler(authority: PlanAuthority) -> Callable[[Dict[str, Any]], Any]:
    """The update_plan_step handler for one authority — main addresses
    its lane by ordinal, a sub addresses ONLY its minted ids."""
    async def handler(args: Dict[str, Any]) -> str:
        status = str(args.get("status") or "")
        if status not in ("in_progress", "completed", "failed"):
            return ("update_plan_step error: status must be "
                    "in_progress|completed|failed")
        note = args.get("note")
        note_text = str(note).strip()[:500] if note else None
        if authority.is_main:
            try:
                ordinal = int(args.get("ordinal"))
            except (TypeError, ValueError):
                return ("update_plan_step error: 'ordinal' must be an "
                        "integer")
            state = None
            for row in await _main_lane_states(authority.thread_id):
                if row.ordinal == ordinal:
                    state = row
                    break
            if state is None:
                return (f"update_plan_step error: no plan step with "
                        f"ordinal {ordinal}")
        else:
            try:
                state_id = int(args.get("state_id"))
            except (TypeError, ValueError):
                return ("update_plan_step error: 'state_id' must be an "
                        "integer")
            if state_id not in authority.minted:
                return ("update_plan_step error: you may only tick nodes "
                        "your own update_plan added under your fork")
            state = await fetch_state(state_id)
            if state is None:
                return (f"update_plan_step error: state {state_id} is gone")
        complaint = _tick_transition(state, status)
        if complaint is not None:
            return f"update_plan_step error: {complaint}"
        try:
            await tick_step(state.state_id, status,
                            thread_id=authority.thread_id, note=note_text,
                            node=state.node,
                            llm_context_id=authority.llm_context_id)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"update_plan_step error: {type(exc).__name__}: {exc}"
        # Keep the runner's spawn parenting pointed at the open step —
        # and remember the last ticked step as the fallback parent for
        # forks spawned in the same step as their tick.
        if authority.last_state_id is not None:
            authority.last_state_id[0] = state.state_id
        if authority.current_state_id is not None:
            if status == "in_progress":
                authority.current_state_id[0] = state.state_id
            elif authority.current_state_id[0] == state.state_id:
                authority.current_state_id[0] = None
        what = (f"plan step {state.ordinal}" if authority.is_main
                else f"fork node {state.state_id}")
        return f"{what} marked {status}"
    return handler


def plan_update_handler(
    authority: PlanAuthority,
    plan_lock: Optional["asyncio.Lock"] = None,
) -> Callable[[Dict[str, Any]], Any]:
    """The update_plan handler — the authority's write scope, enforced
    structurally (sub handlers cannot reach another node's rows at
    all). Every failure is tool text; the result ends with the current
    rendered lane.

    *plan_lock* serializes MAIN-lane mutations when several main
    conversations run CONCURRENTLY (the runner's parallel wave of
    inline steps): the handler's read-modify-write on the pending lane
    (remove → revise → add, with ordinal shifts) must not interleave
    across wave members. None = the plain sequential lane (nothing to
    serialize)."""
    async def handler(args: Dict[str, Any]) -> str:
        lines: List[str] = []
        if plan_lock is None or not authority.is_main:
            await _run_update(authority, args, lines)
        else:
            async with plan_lock:
                await _run_update(authority, args, lines)
        return "\n".join(lines)
    return handler


async def _run_update(authority: PlanAuthority,
                      args: Dict[str, Any],
                      lines: List[str]) -> None:
    """One update_plan call's body — the shared sequential core (the
    lock wrapper above is pure scheduling)."""
    try:
        if authority.is_main:
            lines.extend(await _main_update(authority, args))
        else:
            lines.extend(await _sub_update(authority, args))
    except Exception as exc:  # noqa: BLE001 — tool text, not a crash
        lines.append(f"update_plan error: {type(exc).__name__}: {exc}")
    lines.append("Current plan:")
    try:
        lines.append(await _rendered(authority))
    except Exception as exc:  # noqa: BLE001 — degrade, don't crash
        lines.append(f"(plan read failed: {type(exc).__name__}: {exc})")


async def _main_update(authority: PlanAuthority,
                       args: Dict[str, Any]) -> List[str]:
    """The MAIN scope: pending-lane removes/revise/adds + fork
    termination. Destructive and retargeting ops run FIRST, against the
    numbering the model sees; ADDS run LAST because a mid-plan insert
    shifts later ordinals (the model's remove/revise in the SAME call
    address the pre-insert numbering). Every op reports its own outcome
    line; ops on started rows are rejected by the pending guards
    (plan_store)."""
    lines: List[str] = []
    remove = args.get("remove_ordinals")
    if isinstance(remove, list) and remove:
        ordinals: Set[int] = set()
        for value in remove:
            try:
                ordinals.add(int(value))
            except (TypeError, ValueError):
                return ["update_plan error: remove_ordinals must be "
                        "integers"]
        states_now = await _main_lane_states(authority.thread_id)
        targets = [s for s in states_now if s.ordinal in ordinals]
        not_pending = [s.ordinal for s in targets if s.status != "pending"]
        if not_pending:
            return [f"update_plan error: step(s) {sorted(not_pending)} "
                    "already started — started steps never move"]
        missing = sorted(ordinals - {s.ordinal for s in states_now})
        if missing:
            return [f"update_plan error: no plan step with ordinal(s) "
                    f"{missing}"]
        settled = await skip_pending_states(
            [s.state_id for s in targets], "removed by plan update",
            thread_id=authority.thread_id)
        lines.append(f"removed {settled} pending step(s): "
                     f"{sorted(ordinals)}")
    revise = args.get("revise")
    if isinstance(revise, dict):
        try:
            ordinal = int(revise.get("ordinal"))
        except (TypeError, ValueError):
            return ["update_plan error: revise.ordinal must be an integer"]
        target = next((s for s in await _main_lane_states(
            authority.thread_id) if s.ordinal == ordinal), None)
        if target is None:
            return [f"update_plan error: no plan step with ordinal "
                    f"{ordinal}"]
        revised = await revise_pending_state(
            target.state_id,
            title=(str(revise["title"]).strip()[:MAX_TITLE_LEN]
                   if revise.get("title") else None),
            notes=revise.get("notes"),
            delegate=(revise.get("delegate_goal")),
            llm_context_id=authority.llm_context_id)
        if revised is None:
            return [f"update_plan error: step {ordinal} is not pending "
                    "(started steps never move)"]
        lines.append(f"revised step {ordinal}")
    steps, error = _parse_add_steps(args)
    if error:
        return lines + [error] if lines else [error]
    if steps:
        states_now = await _main_lane_states(authority.thread_id)
        if len(states_now) + len(steps) > MAX_TOTAL_STEPS:
            return lines + [f"update_plan error: the plan is capped at "
                            f"{MAX_TOTAL_STEPS} steps"]
        after = args.get("after_ordinal")
        after_ordinal: Optional[int] = None
        if after is not None:
            try:
                after_ordinal = int(after)
            except (TypeError, ValueError):
                return lines + ["update_plan error: 'after_ordinal' must "
                                "be an integer"]
            if not any(s.ordinal == after_ordinal for s in states_now):
                return [f"update_plan error: no plan step with ordinal "
                        f"{after_ordinal}"]
        added = await add_plan_states(
            authority.thread_id, steps, after_ordinal=after_ordinal,
            llm_context_id=authority.llm_context_id)
        lines.append(f"added {len(added)} step(s):")
        lines.append(render_added(added))
    run_ids = [str(r) for r in (args.get("cancel_run_ids") or [])
               if str(r).strip()]
    if args.get("cancel_running") is True:
        lines.extend(await _cancel(authority, None))
    elif run_ids:
        lines.extend(await _cancel(authority, run_ids))
    return lines


async def _sub_update(authority: PlanAuthority,
                      args: Dict[str, Any]) -> List[str]:
    """The SUB scope: adds under its own fork, nothing else. Delegation
    markers are refused (a fork node is worked by the subagent itself —
    forking a sub-sub agent goes through the spawn tool, whose child
    state lands under this fork anyway), the fork cap bounds runaway
    investigation, and the minted-id set grows so its tick handler can
    address exactly these nodes."""
    steps, error = _parse_add_steps(args, key="add_nodes")
    if error:
        return [error]
    if not steps:
        return ["update_plan error: this call added nothing — pass "
                "add_nodes (your fork's only update)"]
    if any(s.delegate for s in steps):
        return ["update_plan error: fork nodes are worked by yourself — "
                "fork a sub-sub agent with spawn_subagent instead of "
                "marking a node delegated"]
    existing = await fetch_state(int(authority.fork_state_id or 0))
    if existing is None:
        return ["update_plan error: your fork state is gone"]
    if len(authority.minted) + len(steps) > MAX_FORK_CHILDREN:
        return [f"update_plan error: your fork is capped at "
                f"{MAX_FORK_CHILDREN} nodes"]
    added = await add_plan_states(
        authority.thread_id, steps,
        parent_state_id=authority.fork_state_id,
        llm_context_id=authority.llm_context_id)
    authority.minted.update(s.state_id for s in added)
    return [f"added {len(added)} node(s) under your fork "
            f"(tick them by state id):", render_added(added)]


async def _cancel(authority: PlanAuthority,
                  run_ids: Optional[List[str]]) -> List[str]:
    """Terminate background forks — MAIN only. The runner-bound
    canceller stops the child tasks and settles their fork states
    (None = every running fork); the outcome line tells the model what
    actually stopped."""
    if authority.cancel_runs is None:
        return ["update_plan error: fork cancellation unavailable here"]
    try:
        result = authority.cancel_runs(run_ids)
        if inspect.isawaitable(result):
            result = await result
        stopped = [str(r) for r in (result or [])]
    except Exception as exc:  # noqa: BLE001 — tool text, not a crash
        return [f"update_plan error: cancel failed: "
                f"{type(exc).__name__}: {exc}"]
    if not stopped:
        return ["no running background subagent matched"]
    return [f"terminated background subagent(s): {', '.join(stopped)}"]


# ----------------------------------------------------------------------
# The subagent fork kit — the runner's input hook stamps it on each child
# ----------------------------------------------------------------------

SUBAGENT_PLAN_ADDENDUM = (
    "You run as a FORKED subagent of one plan step. Your final answer "
    "returns to the main thread, so make it self-contained. If your "
    "work hits something that needs further investigation, EXTEND YOUR "
    "OWN FORK ONLY: call update_plan with add_nodes (investigation "
    "nodes under your fork), then work them and tick each with "
    "update_plan_step by its state id. Never touch other steps, nodes, "
    "or agents — your tools are scoped to your fork and reject "
    "anything else."
)

# Appended beside the plan addendum when the child carries the spawn
# tool (below the spawn-depth cap): a subagent's OWN delegation lane —
# sub-sub agents registered under its fork, their final answers
# gathered before it concludes.
SUBAGENT_SPAWN_ADDENDUM = (
    "When a piece of your investigation deserves its own focused "
    "effort, fork a sub-sub agent with spawn_subagent (its lane "
    "registers under your fork); NON-OVERLAPPING sub-studies belong "
    "in ONE step as several spawn calls — sibling spawns run in "
    "parallel, one-at-a-time spawns serialize. You must gather every "
    "fork you spawn — collect_subagents before your final answer — "
    "and read each fork's final answer; your own answer carries their "
    "findings."
)


def main_plan_tools(
    thread_id: str, llm_context_id: Optional[str],
    cancel_runs: Optional[Callable[[Optional[List[str]],
                                    Union[List[str], Any]]]],
    plan_lock: Optional["asyncio.Lock"] = None,
) -> Dict[str, BuiltinTool]:
    """The MAIN-lane kit of the step graph: update_plan (a step's
    lifecycle is RUNNER-side bookkeeping now — the node ticks its own
    state row; the model's plan tool is the rework of the not-yet-run
    part: adds, revise, removes, fork termination) +
    search_thread_context (the thread-internal LLM-traffic search —
    every level's kit carries it, thread-scoped by construction).
    *plan_lock* serializes the main-lane update when several main
    conversations run CONCURRENTLY (the runner's parallel wave of
    inline steps); None on the plain sequential lane."""
    authority = PlanAuthority(
        thread_id=thread_id, llm_context_id=llm_context_id,
        cancel_runs=cancel_runs)
    return {
        "update_plan": BuiltinTool(
            schema=UPDATE_PLAN_SCHEMA,
            handler=plan_update_handler(authority, plan_lock)),
        "search_thread_context": thread_search_tool(thread_id),
    }


def subagent_plan_tools(
    thread_id: str, fork_state_id: int, llm_context_id: Optional[str],
) -> Tuple[Dict[str, BuiltinTool], str]:
    """One spawned child's plan kit: the SUB authority bound to its
    fork state plus the tick/update handlers, the thread-internal search
    (any-depth children search the WHOLE thread's traffic — the main
    lane's and their siblings' included), and the system-prompt
    addendum that teaches them. The runner's input hook stamps these on
    ``SubagentRequest.builtin_tools`` / ``.system`` before the child
    starts, so a child without a plan (degraded run) simply gets none."""
    authority = PlanAuthority(
        thread_id=thread_id, fork_state_id=fork_state_id,
        llm_context_id=llm_context_id)
    tools: Dict[str, BuiltinTool] = {
        "update_plan_step": BuiltinTool(
            schema=SUB_UPDATE_PLAN_STEP_SCHEMA,
            handler=tick_handler(authority)),
        "update_plan": BuiltinTool(
            schema=SUB_UPDATE_PLAN_SCHEMA,
            handler=plan_update_handler(authority)),
        "search_thread_context": thread_search_tool(thread_id),
    }
    return tools, SUBAGENT_PLAN_ADDENDUM


# ----------------------------------------------------------------------
# The external authority — out-of-band topology adds on a RUNNING thread
# ----------------------------------------------------------------------

class ExternalPlanError(ValueError):
    """An external add's specific refusal (the endpoint's 400/409 body):
    the plan not yet persisted, a bad anchor ordinal, vocabulary or cap
    violations — the same contracts the model's own adds run under."""


async def external_add_steps(
    thread_id: str,
    raw_steps: Any,
    *,
    after_ordinal: Optional[int] = None,
) -> List[StateRow]:
    """Add steps to a RUNNING thread's MAIN LANE from outside the
    conversations — the daemon's external-topology entry point. Add-only,
    top-lane-only, same validation/caps as the model's update_plan adds
    (business node names, MAX_ADDS_PER_CALL a call, MAX_TOTAL_STEPS a
    thread); refused while the plan phase has not persisted its rows yet
    (an append there would collide ordinals with the incoming plan).

    GUARANTEES (what the caller may rely on): settled rows are history —
    nothing here changes a status, a timestamp or a context pointer;
    later rows only shift ORDINALS (the living plan's position
    semantics); fork subtrees are untouched (the shift filters
    parent_state_id IS NULL, adds are top-level); and the running graph
    executes the new nodes on its next hop (the router re-reads the
    tree between hops). Raises ExternalPlanError with the actionable
    refusal; DB failures propagate as-is (the endpoint maps them 500).
    """
    states_now = await _main_lane_states(thread_id)
    if not states_now:
        raise ExternalPlanError(
            "the thread's plan has no steps yet — external adds are "
            "refused until the plan phase persists (an append now would "
            "collide ordinals with the incoming plan)")
    steps, error = _parse_add_steps({"add_steps": raw_steps})
    if error:
        raise ExternalPlanError(error.split("error: ", 1)[-1])
    if len(states_now) + len(steps) > MAX_TOTAL_STEPS:
        raise ExternalPlanError(
            f"the plan is capped at {MAX_TOTAL_STEPS} steps "
            f"({len(states_now)} exist)")
    if after_ordinal is not None \
            and not any(s.ordinal == after_ordinal for s in states_now):
        raise ExternalPlanError(
            f"no main-lane step with ordinal {after_ordinal}")
    return await add_plan_states(
        thread_id, steps, after_ordinal=after_ordinal)
