"""PlanAuthority — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.planning.authorities` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.plan_json import parse_update_steps
from llm_agents.deep_agents.planning.store import PLAN_NODE, PLAN_STORE, PlannedStep, StateRow


class ExternalPlanError(ValueError):
    """An external add's specific refusal (the endpoint's 400/409 body):
    the plan not yet persisted, a bad anchor ordinal, vocabulary or cap
    violations — the same contracts the model's own adds run under."""


MAX_ADDS_PER_CALL = 6


TICKABLE_FROM: Dict[str, Set[str]] = {
    "pending": {"in_progress", "completed", "failed"},
    "in_progress": {"completed", "failed"},
}


def render_added(states: List[StateRow]) -> str:
    """Newly minted nodes as their add result echoes them (state ids the
    subagent addresses its ticks by)."""
    return "\n".join(
        f"- state {s.state_id} [{s.status}] {s.node} — {s.title}"
        for s in states)


def render_plan(states: List[StateRow]) -> str:
    """The MAIN lane as the model reads it: ordinal, status, node, title
    (children of forks are deliberately absent — their lanes belong to
    the subagents that own them)."""
    if not states:
        return "(the plan has no steps)"
    return "\n".join(
        f"{s.ordinal}. [{s.status}] {s.node} — {s.title}" for s in states)


@dataclass
class PlanAuthority:
    """One conversation's write scope over the thread's plan graph.

    ``main`` authorities own the pending top-level lane plus fork
    termination; ``sub`` authorities own exactly the nodes they minted
    under their fork state. The runner mints one per conversation and
    the plan tools (``tools.plan_tools``) close over it — the model
    never sees a scope choice."""

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

    @staticmethod
    async def _main_lane_states(thread_id: str) -> List[StateRow]:
        """The main lane AS THE MODEL SEES IT: the top-level rows minus the
        runner's own planning-node row (begin_thread's ordinal-0 'plan' row
        — run bookkeeping, not a planned step; the model must neither read
        nor address it)."""
        return [state for state in await PLAN_STORE.list_thread_states(thread_id)
                if state.node != PLAN_NODE]

    @staticmethod
    def _parse_add_steps(arguments: Dict[str, Any],
                         key: str = "add_steps") -> Tuple[List[PlannedStep], str]:
        """The add payload through the plan-JSON vocabulary validation
        (business node names, per-call cap) — shared by the model's update
        tools and the external authority."""
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

    @staticmethod
    async def external_add_steps(
        thread_id: str,
        raw_steps: Any,
        *,
        after_ordinal: Optional[int] = None,
    ) -> List[StateRow]:
        """Add steps to a RUNNING thread's MAIN LANE from outside the
        conversations — the daemon's external-topology entry point. Add-only,
        top-lane-only, same validation/caps as the model's update_plan adds
        (business node names, MAX_ADDS_PER_CALL a call, the configured
        max_total_plan_steps a thread); refused while the plan phase has not
        persisted its rows yet (an append there would collide ordinals with
        the incoming plan).

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
        max_total = run_limits().max_total_plan_steps
        if len(states_now) + len(steps) > max_total:
            raise ExternalPlanError(
                f"the plan is capped at {max_total} steps "
                f"({len(states_now)} exist)")
        if after_ordinal is not None \
                and not any(s.ordinal == after_ordinal for s in states_now):
            raise ExternalPlanError(
                f"no main-lane step with ordinal {after_ordinal}")
        return await PLAN_STORE.add_plan_states(
            thread_id, steps, after_ordinal=after_ordinal)

    @staticmethod
    def tick_transition(state: StateRow, status: str) -> Optional[str]:
        """The lifecycle guard: None = allowed, else the model-facing
        complaint (a settled or regressive tick is rejected, not
        written)."""
        allowed = TICKABLE_FROM.get(state.status)
        if allowed is None:
            return (f"step is already settled ({state.status}) — settled "
                    "steps never move")
        if status not in allowed:
            return (f"a {state.status} step cannot move to {status} "
                    "(lifecycle is pending -> in_progress -> "
                    "completed/failed)")
        return None

    async def rendered(self) -> str:
        """The authority's lane AS THE MODEL SEES IT — the main lane for
        a main authority, the sub's minted nodes only for a fork."""
        if not self.is_main:
            nodes: List[StateRow] = []
            for state_id in sorted(self.minted):
                state = await PLAN_STORE.fetch_state(state_id)
                if state is not None:
                    nodes.append(state)
            return render_added(nodes) if nodes \
                else "(no nodes under your fork)"
        return render_plan(await self._main_lane_states(self.thread_id))

    async def sub_add(self, args: Dict[str, Any]) -> List[str]:
        """The SUB scope: adds under its own fork, nothing else.
        Delegation markers are refused (a fork node is worked by the
        subagent itself — forking a sub-sub agent goes through the spawn
        tool, whose child state lands under this fork anyway), the fork
        cap bounds runaway investigation, and the minted-id set grows so
        the fork's tick tool can address exactly these nodes."""
        steps, error = self._parse_add_steps(args, key="add_nodes")
        if error:
            return [error]
        if not steps:
            return ["update_plan error: this call added nothing — pass "
                    "add_nodes (your fork's only update)"]
        if any(s.delegate for s in steps):
            return ["update_plan error: fork nodes are worked by "
                    "yourself — fork a sub-sub agent with spawn_subagent "
                    "instead of marking a node delegated"]
        existing = await PLAN_STORE.fetch_state(int(self.fork_state_id or 0))
        if existing is None:
            return ["update_plan error: your fork state is gone"]
        max_fork = run_limits().max_fork_children
        if len(self.minted) + len(steps) > max_fork:
            return [f"update_plan error: your fork is capped at "
                    f"{max_fork} nodes"]
        added = await PLAN_STORE.add_plan_states(
            self.thread_id, steps,
            parent_state_id=self.fork_state_id,
            llm_context_id=self.llm_context_id)
        self.minted.update(s.state_id for s in added)
        return [f"added {len(added)} node(s) under your fork "
                f"(tick them by state id):", render_added(added)]
