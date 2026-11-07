"""llm_agents.deep_agents.langgraph.hooks.forks — ``ForkHooks`` — the fork-registration hooks as ONE
object: every spawned subagent becomes a CHILD STATE under the
step it belongs to (its forked path), mints its OWN LLM context
id, binds its request chain, and gets its fork-scoped plan kit
mounted — recursively for sub-sub agents below the spawn-depth
cap.
"""
from __future__ import annotations

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from langgraph.store.base import BaseStore

from llm_agents._core.request_log import bind_request_chain, end_request_chain
from llm_agents.deep_agents.core.delegation import (
    SubagentRun, format_subagent_result,
)
from llm_agents.deep_agents.core.loop import DeepAgent
from llm_agents.deep_agents.langgraph.prompts import standing_system
from llm_agents.deep_agents.langgraph.state_store import (
    entry_checkpoint_id, new_context_id,
)
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.plan_json import (
    MAX_NODE_LEN, MAX_TITLE_LEN,
)
from llm_agents.deep_agents.planning.store import (
    AGENT_PROMPT_FORK_PLAN_ADDENDUM,
    AGENT_PROMPT_FORK_SPAWN_ADDENDUM,
    AGENT_PROMPT_SUBAGENT_SYSTEM,
    PlannedStep,
    PLAN_STORE,
)
from llm_agents.deep_agents.tools.plan_tools import (
    SUBAGENT_SPAWN_ADDENDUM, ForkPlanToolKit,
)
from llm_agents.deep_agents.tools.kg_tools import KgToolSet
from llm_agents.deep_agents.tools.stats_tools import StatsToolSet

logger = logging.getLogger("deep_agents.langgraph.hooks")
from llm_agents.deep_agents.langgraph.hooks.slug import (  # noqa: E402
    _fork_node_slug,
)


class ForkHooks:
    """The fork-registration hooks as ONE object (2026-11-06 — the
    former ``subagent_hooks`` factory returning three closures): the
    captured wiring (thread ids, store, prompt maps, the pass-down
    context, the extra tools) is the object's state and the three hooks
    are its methods — ``input_hook`` registers each spawned subagent as
    a child state under THE STEP the spawn belongs to (``step_state_id``
    — the step hop knows its own row; a missing/None id parents
    top-level, the degraded no-plan case) — its FORKED PATH — with its
    final answer (or error) as the child's subagent-result context;
    both fail-soft.

    The input hook also MOUNTS THE CHILD'S PLAN KIT
    (``tools.plan_tools.ForkPlanToolKit``): the fork-scoped
    update_plan/update_plan_step tools plus the system addendum that
    teaches them, stamped on ``SubagentRequest.builtin_tools`` /
    ``.system`` — so every subagent may extend its OWN fork when its
    work hits something needing further investigation, and nothing else
    (the authority enforces it structurally). ``state_by_run`` is the
    fork broker's cancel-settle map (a private dict when None).

    ``extra_builtin_tools`` is the DYNAMIC prompt-loading rung's mount:
    builtin pairs (``load_prompt`` over the run's catalog + shared
    cache) merged into every child BESIDE its fork kit — and through
    the recursion below, into every sub-sub child — so the ladder
    reaches whatever depth spawns. A child without a plan state row
    (degraded run) still receives the extras alone.

    ``preplanned_steps`` is the FORK PRE-PLANNER's outcome (the
    delegated hop's small parent-side call): the fork's sub-steps,
    materialized as PENDING children under the child state the moment
    it registers, plus a system note telling the executor its plan
    already exists (work + tick, never re-plan first). The structural
    fix for forks whose single conversation burned its hop budget
    self-planning; None (the default, every other caller) keeps the
    child self-planning contract untouched. Fail-soft like the row
    insert: a failed materialization only loses the head start.

    ``biz_context`` is the PARENT'S PASS-DOWN: the run's business
    input (goal + structured context + the rendered texts of the
    loaded prompts — run-level AND this fork's goal-matched ones —
    plus the not-loaded rows' catalog lines) stamped on
    ``SubagentRequest.data`` — beside its fork goal, every child (and,
    through the recursion, every sub-sub child) receives the prompts
    the fork's work runs under. None = plain non-thread usage (the
    child sees only its goal).

    ``agent_prompts`` is the run-start resolution of the agent's
    INTERNAL prompt rows (llm.ai_prompts prompt_type 3, seeded from
    the Python constants by static/ai_prompts/01_deep_agent_prompts.sql):
    the child's base system prompt and the fork addenda come from here
    when their keys are present, falling back to the built-in constants
    otherwise.

    Below the spawn-depth cap the child also receives ITS OWN hook
    object (:meth:`child_hooks`, stamped on
    ``SubagentRequest.subagent_*_hook``): the child's
    spawns become child states under ITS state row — the sub-sub forked
    paths of the thread tree — with their contexts parented at the
    child's context, so every rung of the hierarchy registers the same
    way the top-level forks do.

    ONE NODE ONE LLM CONTEXT, the subagent case: a child is its own
    conversation, so the input hook mints the child's OWN context id
    (stored on the child state row; the fork's added nodes stamp it
    too) and the output hook closes its registry item — counts only,
    the messages never persisted. The RUN WRAPPER binds the child's
    request-log chain (chain_id = the child's context id) inside the
    child task — the child's llm.llm_requests rows carry its own
    context id, not the parent step context's."""

    def __init__(
        self,
        thread_id: str, step_state_id: Optional[int],
        *,
        store: Optional[BaseStore] = None,
        parent_context_id: Optional[str] = None,
        model: Optional[str] = None,
        state_by_run: Optional[Dict[str, int]] = None,
        context_by_run: Optional[Dict[str, Optional[str]]] = None,
        biz_context: Optional[Dict[str, Any]] = None,
        agent_prompts: Optional[Dict[str, str]] = None,
        tool_prompts: Optional[Dict[str, str]] = None,
        extra_builtin_tools: Optional[Dict[str, Any]] = None,
        preplanned_steps: Optional[List[PlannedStep]] = None,
    ) -> None:
        self._thread_id = thread_id
        self._step_state_id = step_state_id
        self._store = store
        self._parent_context_id = parent_context_id
        self._model = model
        # The fork broker's cancel-settle maps, SHARED with the caller
        # when given (the broker keys the cancel settle through them);
        # private dicts for plain non-thread usage.
        self._state_by_run: Dict[str, int] = (
            {} if state_by_run is None else state_by_run)
        self._context_by_run: Dict[str, Optional[str]] = (
            {} if context_by_run is None else context_by_run)
        self._biz_context = biz_context
        self._agent_prompts = agent_prompts
        self._tool_prompts = tool_prompts
        self._extra_builtin_tools = extra_builtin_tools
        self._preplanned_steps = preplanned_steps

    def child_hooks(
        self, state_id: int, context_id: Optional[str],
    ) -> "ForkHooks":
        """The sub-sub child's own hooks: parented at the child's state
        row and context, the pass-down and tool mounts inherited. The
        trio recurses naturally — a depth-1 child's input hook stamps
        nothing at depth 2, the cap."""
        return ForkHooks(
            self._thread_id, state_id,
            store=self._store, parent_context_id=context_id,
            model=self._model, biz_context=self._biz_context,
            agent_prompts=self._agent_prompts,
            tool_prompts=self._tool_prompts,
            extra_builtin_tools=self._extra_builtin_tools)

    async def input_hook(self, run: SubagentRun) -> None:
        thread_id = self._thread_id
        store = self._store
        biz_context = self._biz_context
        agent_prompts = self._agent_prompts
        state_by_run = self._state_by_run
        context_by_run = self._context_by_run
        context_id = new_context_id() if store is not None else None
        # The parent's prompt pass-down: the child sees its fork goal
        # AND the run's business context (rendered into its first user
        # message as the structured-context block). Never overwrite a
        # context a previous hook layer already stamped.
        if run.request.data is None and biz_context:
            run.request.data = dict(biz_context)
        # The child's base prompt: the DB-managed internal row when
        # active (static/ai_prompts/01_deep_agent_prompts.sql), else
        # the built-in constant the request was built with — opened by
        # the STANDING prompt every agent of the run shares.
        base = (agent_prompts or {}).get(AGENT_PROMPT_SUBAGENT_SYSTEM)
        run.request.system = (
            f"{standing_system(agent_prompts)}\n\n"
            f"{base or run.request.system}")
        state_id: Optional[int] = None
        try:
            state_id = await PLAN_STORE.create_child_state(
                thread_id, self._step_state_id,
                _fork_node_slug(run.request.goal),
                run.request.goal[:MAX_TITLE_LEN],
                llm_context_id=context_id)
            state_by_run[run.run_id] = state_id
            context_by_run[run.run_id] = context_id
        except Exception as exc:  # noqa: BLE001 — degrade, don't veto
            logger.warning("child state create failed for subagent %s: %s",
                           run.run_id, exc)
        # THE PRE-PLANNED FORK: the delegated hop's small parent-side
        # sub-plan call already proposed this fork's sub-steps — land
        # them as PENDING children under the just-created row (the UI
        # graph shows the fork's outline at spawn), and flag the note
        # below so the executor is told to WORK, not to re-plan.
        preplanned_ok = False
        if self._preplanned_steps and state_id is not None:
            try:
                await PLAN_STORE.add_plan_states(
                    thread_id,
                    self._preplanned_steps[:run_limits().max_fork_children],
                    parent_state_id=state_id,
                    llm_context_id=context_id)
                preplanned_ok = True
            except Exception as exc:  # noqa: BLE001 — head start only
                logger.warning("preplanned sub-steps insert failed for "
                               "subagent %s (%s) — the child plans "
                               "itself", run.run_id, exc)
        # The fork kit rides even when the state row failed (the
        # authority still confines writes to the fork's own adds — a
        # lost row only loses the audit trail, fail-soft like the rest).
        fork_tools: Dict[str, Any] = {}
        if state_id is not None:
            kit = ForkPlanToolKit(thread_id, state_id, context_id,
                                  descriptions=self._tool_prompts)
            fork_tools = kit.builtin_tools()
            # Forks do the investigation — the stats study tools ride
            # every child's conversation too, and so does the KG
            # search kit (find the right tables by business meaning).
            fork_tools.update(
                StatsToolSet(
                    descriptions=self._tool_prompts).builtin_tools())
            fork_tools.update(
                KgToolSet(
                    descriptions=self._tool_prompts).builtin_tools())
            addendum = ((agent_prompts or {}).get(
                AGENT_PROMPT_FORK_PLAN_ADDENDUM) or kit.prompt_addendum())
        else:
            addendum = ""
        # The dynamic prompt-loading rung rides EVERY child (plan row
        # or not) beside the kits.
        if self._extra_builtin_tools:
            merged = dict(fork_tools)
            merged.update(self._extra_builtin_tools)
            run.request.builtin_tools = merged
        elif fork_tools:
            run.request.builtin_tools = fork_tools
        if state_id is not None:
            spawn_addendum = ""
            if run.request.depth < run_limits().max_spawn_depth:
                # The child can fork sub-sub agents: teach it, and hand
                # it its OWN hooks so its spawns register as child
                # states under its state row (parent context = its
                # context) — the business context passes down with it.
                child = self.child_hooks(state_id, context_id)
                run.request.subagent_input_hook = child.input_hook
                run.request.subagent_output_hook = child.output_hook
                run.request.subagent_run_wrapper = child.run_wrapper
                spawn_addendum = ((agent_prompts or {}).get(
                    AGENT_PROMPT_FORK_SPAWN_ADDENDUM)
                    or SUBAGENT_SPAWN_ADDENDUM)
            run.request.system = (
                f"{run.request.system}\n\n{addendum}{spawn_addendum}"
                + ("\n\n[PRE-PLANNED FORK: your sub-steps are ALREADY "
                   "laid out as the pending nodes under you — the goal's "
                   "'first plan your own sub-steps' clause is ALREADY "
                   "DONE. Do NOT call update_plan to re-plan; start "
                   "working the pending nodes in order, ticking each as "
                   "its goal settles (update_plan stays available to "
                   "extend or rework LATER nodes).]"
                   if preplanned_ok else ""))

    def run_wrapper(self, run: SubagentRun) -> Any:
        """The child's request-log chain binding, applied inside the
        child's task (agent.SubagentRunWrapper). None = no context was
        minted (store-less run) — the child's requests then ride the
        inherited execute chain, an acceptable degrade."""
        context_id = self._context_by_run.get(run.run_id)
        return None if context_id is None else bind_request_chain(context_id)

    async def output_hook(self, run: SubagentRun) -> str:
        # The child's last request is recorded by now — its chain's
        # dedupe cache entry is dead weight; drop it.
        end_request_chain(self._context_by_run.get(run.run_id))
        state_id = self._state_by_run.get(run.run_id)
        if state_id is not None:
            try:
                await PLAN_STORE.finish_child_state(
                    state_id,
                    "completed" if run.status == "completed" else "failed",
                    answer=run.answer, error=run.error,
                    thread_id=self._thread_id,
                    node=_fork_node_slug(run.request.goal),
                    llm_context_id=self._context_by_run.get(run.run_id))
            except Exception as exc:  # noqa: BLE001 — degrade, don't lose
                logger.warning("child state finish failed for subagent "
                               "%s: %s", run.run_id, exc)
        context_id = self._context_by_run.get(run.run_id)
        if state_id is not None and context_id is not None:
            try:
                # The child runs inside its step's superstep — the entry
                # checkpoint id HERE is that superstep's state id. The
                # registry item wears the same business slug as the
                # child state row.
                await self._store.record_subagent_context(
                    self._thread_id, context_id,
                    state_id=state_id,
                    parent_context_id=self._parent_context_id,
                    checkpoint_id=entry_checkpoint_id(), model=self._model,
                    n_llm_calls=run.steps,
                    n_tool_calls=run.tool_call_count,
                    node=_fork_node_slug(run.request.goal))
            except Exception as exc:  # noqa: BLE001 — registry is bookkeeping
                logger.warning("child context item failed for subagent "
                               "%s: %s", run.run_id, exc)
        return format_subagent_result(run)


# ----------------------------------------------------------------------
# The graph
# ----------------------------------------------------------------------

