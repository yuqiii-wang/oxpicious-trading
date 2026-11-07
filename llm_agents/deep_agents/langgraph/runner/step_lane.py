"""llm_agents.deep_agents.langgraph.runner.step_lane — the STEP LANE mixin (the looping heart of every
execution graph): ONE planned step per hop — inline conversations,
delegated forks, the parallel wave — with the fresh-tree routing
between hops.

"""
from __future__ import annotations

from __future__ import annotations

import asyncio
import time
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import (Any, Callable, Dict, List, Optional, Set, Tuple,
                     TypedDict)

from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore

from llm_agents._core.request_log import (
    bind_request_chain, end_request_chain,
)
from llm_agents.deep_agents.core.loop import (
    DEFAULT_SYSTEM_PROMPT,
    AgentResult,
    DeepAgent,
)
from llm_agents.deep_agents.core.mcp import McpToolClient
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.core.sandbox import Sandbox
from llm_agents.deep_agents.langgraph.hooks import ForkBroker, ForkHooks
from llm_agents.deep_agents.langgraph.compaction import compact_findings, should_compact
from llm_agents.deep_agents.langgraph.prompts import (
    CUSTOM_OUTPUT_DIRECTIVE, RESULT_OUTPUT_DIRECTIVE, STEP_SYSTEM_ADDENDUM,
    StepPromptContext, finding_line, standing_system, step_user_message,
)
from llm_agents.deep_agents.langgraph.state_store import (
    NODE_COMPACT, NODE_EXECUTE, entry_checkpoint_id, NODE_FINALIZE, NODE_PLAN,
    NODE_OUTPUT, NODE_RESULT, NODE_SUPERVISOR, DeepAgentStore, NodeRunWriter,
)
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.agents import (
    OutputAgent, OutputDeps, PlanAgent, StepAgent, StepDeps,
    SupervisorAgent,
)
from llm_agents.deep_agents.supervisor import SupervisorSession  # noqa: F401 (tests)
from llm_agents.deep_agents.supervisor.repair import (
    ACTION_IGNORE, ACTION_RETRY, RepairChannel,
)
from llm_agents.deep_agents.planning.plan_json import REVIEW_NODE_TAGS
from llm_agents.deep_agents.planning.planner import (
    Planner, PlanPromptAssembler, render_template, template_args,
)
from llm_agents.deep_agents.planning.prompt_loading import (
    CatalogRow, PromptLadder,
)
from llm_agents.deep_agents.planning.session import (
    ForkPassDown, PlanSessionResult,
    item_labels,
)
from llm_agents.deep_agents.planning.store import (
    AGENT_PROMPT_COMPACT_SYSTEM,
    AGENT_PROMPT_CUSTOM_OUTPUT_DIRECTIVE,
    AGENT_PROMPT_FORK_PLAN_ADDENDUM,
    AGENT_PROMPT_FORK_SPAWN_ADDENDUM,
    AGENT_PROMPT_PLAN_BATCH_FOLLOWUP,
    AGENT_PROMPT_PLAN_REPAIR_MESSAGE,
    AGENT_PROMPT_PLAN_SYSTEM,
    AGENT_PROMPT_RESULT_OUTPUT_DIRECTIVE,
    AGENT_PROMPT_STEP_ADDENDUM,
    AGENT_PROMPT_SUBAGENT_SYSTEM,
    AGENT_PROMPT_SUBPLAN_SYSTEM,
    PLAN_NODE,
    PlannedStep,
    StateRow,
    ThreadPlan,
    identities_from_scope,
    extract_critical,
    PLAN_STORE,
)
from llm_agents.deep_agents.tools.kg_tools import KgToolSet
from llm_agents.deep_agents.tools.plan_tools import MainPlanToolKit
from llm_agents.deep_agents.tools.prompt_tools import PromptLoadToolKit
from llm_agents.deep_agents.tools.stats_tools import StatsToolSet

logger = logging.getLogger("deep_agents.runner.step_lane")

from llm_agents.deep_agents.langgraph.runner.state import (  # noqa: E402
    CUSTOM_OUTPUT_SPEC, GraphOutputSpec, RESULT_OUTPUT_SPEC,
    ThreadState, _RunContext,
)


class StepLane:
    async def step_node(self, state: ThreadState) -> Dict[str, Any]:
        """ONE planned research lane per hop: tick in_progress
        (runner-side), run the step's engine (a fresh conversation for
        an inline step; a supervised subagent for a delegated one), settle
        the row with the finding, append the finding to the state, and
        route by the fresh plan tree. A hop failure settles the step
        failed and ENDS THE MAIN PATH — the remaining pending steps
        settle not-run (skipped with the reason) and the chain goes to
        finalize; the run survives (error-as-state, always-answer), and
        the SUPERVISOR WATCHDOG (outside the graph — see run_thread)
        wakes on the failure and records its diagnosis.

        THE PARALLEL WAVE: when the head of the runnable lane is
        delegated or parallel-marked, every CONSECUTIVE eligible step
        joins one parallel wave — sibling fork goals are fixed at plan
        time and parallel marks carry the planner's independence
        assertion, so neither can depend on sibling findings; the wave
        stops at the first unmarked inline step (the dependency
        barrier) or a delegate read that fails. Members run
        concurrently under the configured max_parallel_delegated_steps
        slots (inline
        members' update_plan writes serialized by one lock); findings
        append in ORDINAL order whatever the completion order, so the
        lane reads exactly as it would have serially."""
        thread_id = state.get("thread_id") or ""
        # THE RUN'S TOTAL WALL-CLOCK BUDGET (run_timeout_sec, default
        # 5h): checked every hop — past it the chain routes to finalize
        # NOW (the pending tail settles not-run, the run's answer is
        # the last completed finding) instead of grinding on. The
        # bridge's and the browser client's deadlines sit AT the same
        # value, so whoever fires first the run settles cleanly.
        run_started = state.get("run_started")
        if (isinstance(run_started, (int, float))
                and time.monotonic() - run_started
                > run_limits().run_timeout_sec):
            elapsed_h = (time.monotonic() - run_started) / 3600.0
            logger.warning(
                "run %s exceeded the %.1fh total budget — routing to "
                "finalize (pending steps settle not-run)",
                thread_id, elapsed_h)
            return {"next": "finalize",
                    "run_error": (
                        f"run exceeded the "
                        f"{run_limits().run_timeout_sec / 3600.0:.0f}h "
                        "total budget — settled at the last completed "
                        "step")}
        try:
            tree = await PLAN_STORE.list_thread_states(thread_id)
        except Exception as exc:  # noqa: BLE001 — cannot even read the plan
            logger.warning("step lane: plan read failed: %s", exc)
            return {"next": "finalize",
                    "run_error": f"plan read failed: {exc}"}
        runnable = sorted(
            (s for s in tree
             if s.status in ("pending", "in_progress")),
            key=lambda s: (s.ordinal, s.state_id))
        if not runnable:
            # Nothing runnable at lane entry (an empty or fully settled
            # tree): the chain ends — finalize; the supervisor watchdog
            # (run_thread's tail, outside the graph) reads whatever
            # settled.
            return {"next": "finalize"}
        step = runnable[0]
        limits = run_limits()
        common = dict(
            model=state.get("model"),
            # Per-conversation cap: generous since every request now
            # carries the self._provider's per-request token ceiling (the
            # runaway guard lives THERE — core.self._provider — not in step
            # counts). The run-level bound is the plan itself (≤ the
            # configured max_plan_nodes hops); this clamp only keeps
            # one conversation from eating the whole run (live runs
            # showed 12 starving delegated data pulls; 20 still bit
            # deep studies).
            max_steps=max(limits.min_conversation_steps, min(
                int(state.get("max_steps")
                    or limits.max_conversation_steps),
                limits.max_steps_cap)),
            temperature=state.get("temperature"),
            # The parent-side supervision policy (propagates to every
            # spawned child with the hooks).
            max_concurrent_subagents=limits.max_concurrent_subagents,
            subagent_timeout=limits.subagent_timeout_sec,
        )

        async def _fetch_delegate(
                state_row: StateRow) -> Tuple[Optional[str], bool]:
            try:
                return await PLAN_STORE.fetch_step_delegate(state_row.state_id)
            except Exception as exc:  # noqa: BLE001 — inline fallback
                logger.warning("delegate fetch failed for step %s: %s",
                               state_row.state_id, exc)
                return None, False

        findings = list(state.get("findings") or [])
        n_tool_calls = int(state.get("n_tool_calls") or 0)
        n_llm_calls = int(state.get("steps") or 0)
        # The fork pass-down: the run's business context + the loaded
        # prompts' rendered texts + the not-loaded rows' catalog lines —
        # every child this hop spawns (and every sub-sub child below
        # it) receives them beside its goal.
        prompt_texts = dict(state.get("prompt_texts") or {})
        prompt_catalog = dict(state.get("prompt_catalog") or {})
        biz_context = ForkPassDown.pack(
            state.get("goal", ""), state.get("data"), prompt_texts,
            prompt_catalog).to_context()
        # The internal prompt rows resolved at run start (missing key =
        # the built-in constant, resolved at each use below).
        agent_prompts = dict(state.get("agent_prompts") or {})

        # ------------------------------------------------------------------
        # One delegated step's execution — the wave member and (wave of
        # one) the plain delegated hop. Never raises: every failure
        # settles this member's row failed and returns its finding, so
        # one bad fork never takes the wave (or the lane) down.
        # ------------------------------------------------------------------
        async def _delegated_hop(step_row: StateRow,
                                 delegate: str) -> Tuple[str, str, int, int]:
            writer = NodeRunWriter(
                self._store, thread_id, f"step-{step_row.state_id}",
                model=state.get("model"), llm=False,
                state_id=step_row.state_id)
            await writer.begin(entry_checkpoint_id())
            try:
                await PLAN_STORE.tick_step(step_row.state_id, "in_progress",
                                thread_id=thread_id, node=step_row.node,
                                llm_context_id=writer.context_id)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning("step in_progress tick failed (%s): %s",
                               step_row.state_id, exc)
            # The fork pass-down: the run's business context + the
            # selected prompts' rendered texts — every child this hop
            # spawns (and every sub-sub child below it) receives it
            # beside its goal. THE DYNAMIC RUNG rides the GOAL: a
            # delegated goal is this fork's whole directive, and a
            # sub-plan goal NAMES its study prompt keys — those (and
            # any label hits on the goal's vocabulary) load for THIS
            # child ahead of its conversation, merged into its
            # pass-down; the child (and its own sub-children, through
            # the extra tools below) still carry load_prompt for what
            # the code could not see.
            node_prompts = await self._node_prompts(state, delegate)
            child_biz_context = (
                ForkPassDown.pack(
                    state.get("goal", ""), state.get("data"),
                    {**prompt_texts, **node_prompts},
                    {k: v for k, v in prompt_catalog.items()
                     if k not in node_prompts}).to_context()
                if node_prompts else biz_context)
            load_tools = self._prompt_kit(state).builtin_tools()
            # THE FORK PRE-PLANNER: one small parent-side call proposes
            # the fork's sub-steps NOW (see _preplan_fork) — the child
            # registers with its plan already materialized and works
            # instead of spending its conversation budget planning.
            # Fail-soft: any failure (transport, budget, bad JSON after
            # the self._repair round) falls back to the child self-planning
            # contract unchanged.
            preplanned: Optional[List[PlannedStep]] = None
            try:
                preplanned = await Planner.preplan_fork(
                    self._provider.chat_complete, delegate, state.get("data"),
                    model=state.get("model"),
                    system_prompt=agent_prompts.get(AGENT_PROMPT_SUBPLAN_SYSTEM),
                    repair_template=agent_prompts.get(
                        AGENT_PROMPT_PLAN_REPAIR_MESSAGE))
                logger.info("fork %s pre-planned: %d sub-step(s)",
                            step_row.state_id, len(preplanned))
            except Exception as exc:  # noqa: BLE001 — child plans itself
                logger.warning("fork pre-plan failed for step %s (%s) — "
                               "the child plans itself",
                               step_row.state_id, exc)
            broker = ForkBroker(thread_id=thread_id)
            try:
                # A fork step: NO main conversation — the subagent IS
                # the engine. The step hop carries no context of its
                # own; the child mints its own (registry + requests all
                # hang off the child's id).
                hooks = ForkHooks(
                    thread_id, step_row.state_id,
                    store=self._store, parent_context_id=None,
                    model=state.get("model"),
                    state_by_run=broker.state_by_run,
                    context_by_run=broker.context_by_run,
                    biz_context=child_biz_context,
                    agent_prompts=agent_prompts,
                    tool_prompts=dict(state.get("tool_prompts") or {}),
                    extra_builtin_tools=load_tools,
                    preplanned_steps=preplanned)
                agent = DeepAgent(
                    self._provider, self._mcp,
                    allow_subagents=True,
                    system=f"{standing_system(agent_prompts)}\n\n"
                           f"{DEFAULT_SYSTEM_PROMPT}",
                    sandbox=self.run_sandbox(state),
                    tool_prompts=dict(state.get("tool_prompts") or {}),
                    subagent_input_hook=hooks.input_hook,
                    subagent_output_hook=hooks.output_hook,
                    subagent_run_wrapper=hooks.run_wrapper,
                    **common)
                broker.agent = agent
                # A delegated fork (a sub-plan section) spends its first
                # conversation turns planning its own subtree (add_nodes
                # + per-node ticks) before the study begins — give it
                # bounded headroom over the inline clamp (live 2026-11-03
                # run: 12-step forks starved exactly there).
                finding = await agent.run_delegation(
                    delegate, max_steps=min(
                        common["max_steps"] + 8,
                        run_limits().max_steps_cap))
                child = (agent.subagent_runs() or [None])[-1]
                status = ("completed" if child is not None
                          and child.status == "completed" else "failed")
                n_llm = child.steps if child is not None else 0
                n_tool = child.tool_call_count if child is not None else 0
                await writer.finish(
                    status, n_states=1,
                    error=None if status == "completed"
                    else f"delegation failed: "
                         f"{child.error if child is not None else '?'}")
            except Exception as exc:  # noqa: BLE001 — advance, don't die
                logger.warning("step %s (%s) failed, the chain advances: %s",
                               step_row.state_id, step_row.title, exc)
                finding = f"STEP FAILED — {type(exc).__name__}: {exc}"
                status = "failed"
                n_llm = n_tool = 0
                await writer.finish("failed",
                                    error=f"{type(exc).__name__}: {exc}",
                                    n_states=1)
            finally:
                end_request_chain(writer.context_id)
            # The delegation's answer may end with the fork's [CRITICAL]
            # marker (the fork addendum's contract) — strip it from the
            # finding and flag the step's result row with it (the child's
            # own subagent-result row flags at its settle, hooks.py).
            critical, finding = extract_critical(finding)
            try:
                await PLAN_STORE.tick_step(
                    step_row.state_id, status,
                    thread_id=thread_id,
                    node=step_row.node,
                    llm_context_id=writer.context_id,
                    note=" ".join(finding.split())[:500] or None,
                    critical=critical,
                    # The failed settle's reason rides the row's error
                    # column too (the tick note alone left failed rows
                    # with blank errors in the 2026-11-03 audit).
                    error=(" ".join(finding.split())[:300]
                           if status == "failed" else None))
            except Exception as exc:  # noqa: BLE001 — finalize settles it
                logger.warning("step settle tick failed (%s): %s",
                               step_row.state_id, exc)
            return status, finding, n_llm, n_tool

        # ------------------------------------------------------------------
        # One INLINE step's execution — its OWN conversation (the
        # standing system prompt + the step contract; the run goal +
        # structured context + the selected prompts' texts + step
        # directive + accumulated findings as the user message). The
        # wave member for a parallel-marked step and (wave of one) the
        # plain sequential hop. *plan_lock* serializes concurrent wave
        # members' update_plan writes (None on the sequential lane).
        # Never raises — same contract as _delegated_hop.
        # ------------------------------------------------------------------
        # THE STEP AGENT (2026-11-06): the inline hop's conversation
        # as an agent object (agents/step_agent.py — ONE agent == ONE
        # node: identity + its own chained context + resume()). The
        # deps bundle carries this closure scope's collaborators
        # (hooks factory, message builder, tool kits, sandbox, system)
        # so the agent module stays langgraph-free.
        def _step_system() -> str:
            system_args = template_args(state.get("data"))
            base = render_template(
                state.get("system") or DEFAULT_SYSTEM_PROMPT,
                system_args)
            step_addendum = (agent_prompts.get(AGENT_PROMPT_STEP_ADDENDUM)
                             or STEP_SYSTEM_ADDENDUM)
            return (f"{standing_system(agent_prompts)}\n\n"
                    f"{base}\n\n{step_addendum}")

        step_deps = StepDeps(
            make_hooks=lambda step_row, broker, parent_context_id: ForkHooks(
                thread_id, step_row.state_id,
                store=self._store, parent_context_id=parent_context_id,
                model=state.get("model"),
                state_by_run=broker.state_by_run,
                context_by_run=broker.context_by_run,
                biz_context=biz_context,
                agent_prompts=agent_prompts,
                tool_prompts=dict(state.get("tool_prompts") or {}),
                extra_builtin_tools=self._prompt_kit(state).builtin_tools()),
            build_message=lambda step_row, node_prompts, resume_block: (
                step_user_message(StepPromptContext(
                    goal=state.get("goal", ""), data=state.get("data"),
                    state=step_row, findings=findings,
                    prompt_texts=prompt_texts,
                    prompt_catalog=prompt_catalog,
                    node_prompts=node_prompts,
                    digest=state.get("findings_digest"),
                    n_compacted=int(state.get("n_compacted") or 0)))
                + (f"\n\n{resume_block}" if resume_block else "")),
            make_tools=lambda step_row, broker, plan_lock, writer: {
                **MainPlanToolKit(
                    thread_id, writer.context_id, broker.cancel, plan_lock,
                    descriptions=dict(state.get("tool_prompts") or {})
                ).builtin_tools(),
                **StatsToolSet(
                    descriptions=dict(state.get("tool_prompts") or {})
                ).builtin_tools(),
                **KgToolSet(
                    descriptions=dict(state.get("tool_prompts") or {})
                ).builtin_tools(),
                **self._prompt_kit(state).builtin_tools(),
            },
            make_broker=lambda tid: ForkBroker(thread_id=tid),
            get_node_prompts=lambda directive: self._node_prompts(
                state, directive),
            sandbox=lambda: self.run_sandbox(state),
            system=_step_system(),
            common=common,
            mcp=self._mcp,
        )

        async def _inline_hop(
                step_row: StateRow,
                plan_lock: Optional[asyncio.Lock],
        ) -> Tuple[str, str, int, int]:
            # One agent per hop run: its writer factory composes THIS
            # step's node item (step-<state_id>) + model + state row.
            agent = StepAgent(
                self._provider, deps=step_deps, thread_id=thread_id,
                model=state.get("model"),
                writer_factory=lambda: NodeRunWriter(
                    self._store, thread_id, f"step-{step_row.state_id}",
                    model=state.get("model"), llm=True,
                    state_id=step_row.state_id))
            # THE INSTRUCTED RESUME: a supervisor retry verdict left
            # this step's instruction in the state's resume map — the
            # agent re-runs WITH its prior context instead of cold.
            info = (state.get("resume_instructions") or {}).get(
                str(step_row.state_id))
            if info:
                return await agent.resume(
                    step_row,
                    instruction=str(info.get("instruction") or ""),
                    prior_error=str(info.get("prior_error") or ""),
                    prior_context_id=(
                        step_row.llm_context_id or None))
            return await agent.run(step_row)

        head_delegate, head_parallel = await _fetch_delegate(step)
        # Wave eligibility: a DELEGATED step (its goal is fixed at plan
        # time — it cannot depend on sibling findings) or an explicitly
        # parallel-marked INLINE step (the planner asserted
        # independence). An unmarked inline step is the dependency
        # barrier: it may need every earlier finding, so the wave stops.
        async def _wave_flags(
                row: StateRow) -> Tuple[Optional[str], bool]:
            if row.state_id == step.state_id:
                return head_delegate, head_parallel
            return await _fetch_delegate(row)

        if head_delegate is not None or head_parallel:
            # THE PARALLEL WAVE: collect the maximal run of CONSECUTIVE
            # delegated-or-parallel steps, then execute them
            # concurrently under the wave slots — a delegated member
            # runs its fork engine, an inline member its own
            # conversation (their update_plan writes serialized by one
            # lock). THE JOIN — LangGraph's parallel-node semantics:
            # the merge node after the wave waits for ALL of the wave's
            # nodes to finish; the gather below gates the lane, so the
            # next node starts only after EVERY member settled (a
            # failed member settles failed and still gates it — no
            # early merge). Findings append in ordinal order after the
            # gather, so the evidence lane (and the answer pick below)
            # is exactly the serial lane's.
            wave: List[StateRow] = [step]
            delegates: Dict[int, Optional[str]] = {
                step.state_id: head_delegate}
            for candidate in runnable[1:]:
                goal, parallel = await _wave_flags(candidate)
                if goal is None and not parallel:
                    break
                wave.append(candidate)
                delegates[candidate.state_id] = goal
            if len(wave) > 1:
                logger.info("parallel wave: %d step(s) %s run concurrently",
                            len(wave), [w.ordinal for w in wave])
            wave_slots = asyncio.Semaphore(
                run_limits().max_parallel_delegated_steps)
            plan_lock: Optional[asyncio.Lock] = (
                asyncio.Lock() if any(
                    delegates[m.state_id] is None for m in wave) else None)

            async def _wave_member(
                    member: StateRow) -> Tuple[str, str, int, int]:
                async with wave_slots:
                    goal = delegates[member.state_id]
                    if goal is not None:
                        return await _delegated_hop(member, goal)
                    return await _inline_hop(member, plan_lock)

            outcomes = await asyncio.gather(
                *(_wave_member(member) for member in wave),
                return_exceptions=True)
            # A member that still raised (nothing in the hops should)
            # becomes its own failed result — the wave's other members and
            # the lane's advance never depend on one broken step.
            settled: List[Tuple[str, str, int, int]] = []
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    if isinstance(outcome, asyncio.CancelledError):
                        raise outcome
                    logger.warning("parallel wave member raised: %s", outcome)
                    settled.append(
                        ("failed",
                         f"STEP FAILED — {type(outcome).__name__}: {outcome}",
                         0, 0))
                else:
                    settled.append(outcome)
            answer_finding: Optional[str] = None
            for member, (m_status, m_finding, m_llm, m_tool) in zip(
                    wave, settled):
                findings.append(finding_line(member.ordinal, member.title,
                                             m_finding))
                n_llm_calls += m_llm
                n_tool_calls += m_tool
                if m_status == "completed":
                    # The run's answer is the LAST completed step's
                    # finding — within the wave, the highest-ordinal
                    # completed member (later hops override this).
                    answer_finding = m_finding
        else:
            # The plain sequential inline hop — no wave, no lock.
            status, finding, n_llm, n_tool = await _inline_hop(step, None)
            findings = findings + [finding_line(step.ordinal, step.title,
                                                finding)]
            n_llm_calls += n_llm
            n_tool_calls += n_tool
            answer_finding: Optional[str] = (
                finding if status == "completed" else None)

        # Which main-path steps failed this hop — EVERY failed member
        # (wave or sequential, REVIEW WAVE steps INCLUDED: the join
        # below pauses for them too — the supervisor decides retry /
        # ignore before the lane resumes past the barrier). Only the
        # PATH-ENDER classification (after the self._repair verdicts) treats
        # a review member differently: an un-repaired reviewer failure
        # does not end the path — the synthesis runs on the reviews
        # that returned and notes the failed one.
        failed_rows: List[Tuple[StateRow, str]] = []
        # Bound on EVERY hop (the update's carry-forward reads it even
        # when no failure opened a self._repair window — the live 467cc run
        # caught the UnboundLocalError on healthy hops).
        resume_instructions: Dict[str, Dict[str, str]] = {}
        if head_delegate is not None or head_parallel:
            for member, (m_status, m_finding, _m_llm, _m_tool) in zip(
                    wave, settled):
                if m_status == "failed":
                    failed_rows.append((member, m_finding))
        elif status == "failed":
            failed_rows.append((step, finding))

        # The sids this hop actually ran (its wave members, or the one
        # sequential step) — their resume entries are consumed.
        settled_ids_this_hop: Set[int] = (
            {m.state_id for m in wave}
            if head_delegate is not None or head_parallel
            else {step.state_id})

        update: Dict[str, Any] = {
            "findings": findings,
            "steps": n_llm_calls,
            "n_tool_calls": n_tool_calls,
            # THE RESUME MAP's carry-forward: this hop's new retry
            # instructions merge in; entries whose rows settled this
            # hop (resumed to completed/failed, ignored, or stale)
            # drop — only instructions for rows still runnable ride
            # to the next hop (str keys, checkpoint-safe).
            "resume_instructions": {
                **{k: v for k, v in (
                    state.get("resume_instructions") or {}).items()
                    if int(k) not in settled_ids_this_hop},
                **resume_instructions,
            },
        }
        # The run's answer is the LAST COMPLETED step's finding — a
        # failed tail step must not overwrite the best document the run
        # produced (its failure lives in its row and finding line).
        if answer_finding is not None:
            update["answer"] = " ".join(answer_finding.split())
        # The router's input: is anything still runnable in the FRESH
        # tree (this hop's own replan included)? — and does the NEXT
        # hop's context approach the LLM limit (the compact detour)?
        try:
            tree = await PLAN_STORE.list_thread_states(thread_id)
            # THE SUPERVISOR REPAIR WINDOW (2026-11-06): a failed
            # step — wave member OR sequential, review reviewer
            # included — PAUSES the lane at this join (every
            # before-node has settled; the barrier holds the resume)
            # while the poller session beside the graph decides (code
            # gates, then ONE bounded agent call): retry-step has
            # ALREADY re-opened the row (failed → pending; a
            # rate-limit retry is throttled inside the window),
            # ignore-step has ALREADY settled it skipped (the run
            # resumes PAST the node), no-action leaves it failed. The
            # lane stays read-only here; only the PATH-ENDER set
            # below settles the tail. No channel / grace 0 / timeout /
            # no-action verdicts → the dicts are empty and exactly the
            # old semantics follow.
            retried_ids: Set[int] = set()
            ignored_ids: Set[int] = set()
            if failed_rows and self._repair is not None and thread_id:
                prior_errors = {row.state_id: err
                                for row, err in failed_rows}
                try:
                    await self._repair.signal_failures(
                        [(row.state_id, err) for row, err in failed_rows])
                    verdicts = await self._repair.await_verdicts(
                        grace_sec=run_limits().supervisor_repair_grace_sec)
                    retried_ids = {
                        sid for sid, verdict in verdicts.items()
                        if verdict.action == ACTION_RETRY}
                    ignored_ids = {
                        sid for sid, verdict in verdicts.items()
                        if verdict.action == ACTION_IGNORE}
                except Exception as exc:  # noqa: BLE001 — self._repair is a
                    logger.warning(  # lane-shortcut, never the lane
                        "supervisor self._repair window failed: %s", exc)
                resolved = retried_ids | ignored_ids
                if resolved:
                    logger.info(
                        "supervisor self._repair: re-running %s, ignoring %s",
                        sorted(retried_ids), sorted(ignored_ids))
                    failed_rows = [pair for pair in failed_rows
                                   if pair[0].state_id not in resolved]
                    # THE INSTRUCTED RESUME: a retried step's verdict
                    # carries the supervisor's instruction — the
                    # step agent re-runs WITH its prior context (the
                    # instruction + the prior attempt's error + the
                    # prior chain). The map rides the STATE (str keys,
                    # checkpoint-safe) so the NEXT hop — which re-picks
                    # the reopened row off the fresh tree — reads it;
                    # the hop consumes its entry there.
                    for sid in retried_ids:
                        resume_instructions[str(sid)] = {
                            "instruction": verdicts[sid].instruction,
                            "prior_error": prior_errors.get(sid, ""),
                        }
                    tree = await PLAN_STORE.list_thread_states(thread_id)
            # PATH ENDERS — the failures that still end the main path:
            # a non-review step left failed (no self._repair, no ignore). A
            # review member left failed does NOT end the path (the
            # synthesis notes it) and an ignored step never does.
            failed_marks = [
                f"step {row.ordinal} ({row.node})"
                for row, _err in failed_rows
                if row.node not in REVIEW_NODE_TAGS]
            # PATH FAILURE ENDS THE PATH: a failed step settles every
            # remaining pending main-lane row not-run (skipped with the
            # reason) — the doomed tail never burns LLM calls — and the
            # lane ends at finalize; the SUPERVISOR SESSION (the
            # backend poller beside the graph) wakes on the failure
            # and records its diagnosis. Error-as-state holds: rows
            # settle, the run advances; only the lane stops.
            if failed_marks and thread_id:
                try:
                    pending_ids = [s.state_id for s in tree
                                   if s.status == "pending"
                                   and s.state_id not in retried_ids]
                    if pending_ids:
                        await PLAN_STORE.skip_pending_states(
                            pending_ids,
                            "not run — the main path failed at "
                            + ", ".join(failed_marks),
                            thread_id=thread_id)
                        tree = [s for s in tree
                                if s.state_id not in set(pending_ids)]
                except Exception as exc:  # noqa: BLE001 — finalize settles
                    logger.warning("not-run settle failed: %s", exc)
            runnable = sorted(
                (s for s in tree
                 if s.status in ("pending", "in_progress")),
                key=lambda s: (s.ordinal, s.state_id))
            if not runnable:
                # The lane is done — finalize; the supervisor watchdog
                # (run_thread's tail, OUTSIDE the graph) reads the
                # settled tree and intervenes on any root-path failure.
                update["next"] = "finalize"
            else:
                nxt = runnable[0]
                update["next"] = (
                    "compact" if should_compact(
                        model=state.get("model"),
                        goal=state.get("goal", ""),
                        data=state.get("data"),
                        prompt_texts=prompt_texts,
                        prompt_catalog=prompt_catalog,
                        digest=state.get("findings_digest"),
                        findings=findings,
                        directive=f"{nxt.ordinal}. {nxt.title}")
                    else "step")
        except Exception as exc:  # noqa: BLE001 — cannot re-read: stop
            logger.warning("step lane: plan re-read failed: %s", exc)
            update["next"] = "finalize"
        return update

