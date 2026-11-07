"""llm_agents.deep_agents.langgraph.runner.nodes — the graph TAIL mixins: the context-compaction detour,
the degraded execute lane, the FINAL OUTPUT NODE (+ its context
persist), finalize, the two routers, and ``build`` — the
StateGraph wiring every execution graph compiles with.

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

logger = logging.getLogger("deep_agents.runner.nodes")

from llm_agents.deep_agents.langgraph.runner.state import (  # noqa: E402
    CUSTOM_OUTPUT_SPEC, GraphOutputSpec, RESULT_OUTPUT_SPEC,
    ThreadState, _RunContext,
)


class TailNodes:
    async def compact_node(self, state: ThreadState) -> Dict[str, Any]:
        """The CONTEXT-COMPACTION node — the step lane's detour when the
        accumulated findings approach the LLM context limit
        (``compaction.should_compact`` at the router): ONE LLM call
        merges the prior digest + the raw findings tail into a fresh
        bounded digest, the merged raw lines clear, and a visible
        ``compact`` state row slots into the plan tree right after the
        last settled step (pending rows shift up — the canvas chain
        shows the compaction where it ran). Fail-soft at every rung: a
        failed LLM call keeps the raw findings untouched (the render
        aging stays the never-fail backstop) and the chain advances."""
        thread_id = state.get("thread_id") or ""
        findings = [f for f in (state.get("findings") or []) if f]
        digest = state.get("findings_digest")
        try:
            tree = await PLAN_STORE.list_thread_states(thread_id)
        except Exception as exc:  # noqa: BLE001 — cannot even read the plan
            logger.warning("compact node: plan read failed: %s", exc)
            return {"next": "finalize",
                    "run_error": f"plan read failed: {exc}"}
        pending = any(s.status in ("pending", "in_progress") for s in tree)
        if not findings and not digest:
            # Nothing to merge — the router only detours here with
            # evidence; route straight back at the lane.
            return {"next": "step" if pending else "finalize"}

        # The audit row: a top-level node 'compact' state right after
        # the last settled step. Fail-soft — the compaction itself must
        # never depend on the row landing.
        settled = [s.ordinal for s in tree
                   if s.status not in ("pending", "in_progress")]
        after_ordinal = max(settled) if settled else 0
        compact_state_id: Optional[int] = None
        compact_ordinal = after_ordinal + 1
        try:
            rows = await PLAN_STORE.add_plan_states(
                thread_id,
                [PlannedStep(
                    node=NODE_COMPACT,
                    title="context compaction — merge findings "
                          "into the digest")],
                after_ordinal=after_ordinal)
            if rows:
                compact_state_id = rows[0].state_id
                compact_ordinal = rows[0].ordinal
        except Exception as exc:  # noqa: BLE001 — audit trail only
            logger.warning("compact state row insert failed: %s", exc)
        writer = NodeRunWriter(
            self._store, thread_id,
            f"{NODE_COMPACT}-{compact_ordinal}"
            if compact_state_id is not None else NODE_COMPACT,
            model=state.get("model"), state_id=compact_state_id)
        await writer.begin(entry_checkpoint_id())
        if compact_state_id is not None and writer.context_id is not None:
            try:
                await PLAN_STORE.stamp_states_context(
                    [compact_state_id], writer.context_id)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning("compact context stamp failed: %s", exc)
            try:
                await PLAN_STORE.tick_step(compact_state_id, "in_progress",
                                thread_id=thread_id, node=NODE_COMPACT,
                                llm_context_id=writer.context_id)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning("compact in_progress tick failed: %s", exc)

        n_compacted = int(state.get("n_compacted") or 0)
        new_digest: Optional[str] = None
        error: Optional[str] = None
        try:
            with bind_request_chain(writer.context_id):
                new_digest = await compact_findings(
                    self._provider.chat_complete,
                    goal=state.get("goal", ""),
                    digest=digest, findings=findings,
                    model=state.get("model"),
                    system_prompt=(state.get("agent_prompts") or {}).get(
                        AGENT_PROMPT_COMPACT_SYSTEM))
        except Exception as exc:  # noqa: BLE001 — keep the raw findings
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("compaction failed — raw findings kept (the "
                           "aging stays the backstop): %s", exc)
        finally:
            end_request_chain(writer.context_id)
        await writer.finish(
            "failed" if error else "completed",
            n_llm_calls=1, n_states=1 if compact_state_id else 0,
            error=error,
            answer_chars=len(new_digest or ""))
        if compact_state_id is not None:
            note = (f"merged {len(findings)} finding(s) into the digest "
                    f"({n_compacted} → {n_compacted + len(findings)} "
                    f"lines covered)" if error is None else error)
            try:
                await PLAN_STORE.tick_step(
                    compact_state_id,
                    "failed" if error else "completed",
                    thread_id=thread_id, node=NODE_COMPACT,
                    llm_context_id=writer.context_id, note=note)
            except Exception as exc:  # noqa: BLE001 — finalize settles it
                logger.warning("compact settle tick failed: %s", exc)

        update: Dict[str, Any] = {
            "steps": int(state.get("steps") or 0) + 1,
        }
        if new_digest is not None:
            # THE MERGE: the raw lines are folded into the digest and
            # cleared; later hops carry digest + fresh tail until the
            # next compaction folds that tail in turn.
            update["findings_digest"] = new_digest
            update["findings"] = []
            update["n_compacted"] = n_compacted + len(findings)
        # Route by the FRESH tree — the lane continues where it left
        # off, now under the smaller evidence block; a lane-ending
        # compaction goes to finalize (the supervisor watchdog outside
        # the graph reads the settled tree after).
        try:
            tree = await PLAN_STORE.list_thread_states(thread_id)
            update["next"] = ("step" if any(
                s.status in ("pending", "in_progress") for s in tree)
                else "finalize")
        except Exception as exc:  # noqa: BLE001 — cannot re-read: stop
            logger.warning("compact node: plan re-read failed: %s", exc)
            update["next"] = "finalize"
        return update

    async def execute_node(self, state: ThreadState) -> Dict[str, Any]:
        """The DEGRADED lane — the plan never persisted (proposal or
        persistence failure): the old one-conversation node with no
        plan tools, the pre-thread fail-soft backstop."""
        thread_id = state.get("thread_id") or ""
        writer = NodeRunWriter(self._store, thread_id, NODE_EXECUTE,
                               model=state.get("model"))
        await writer.begin(entry_checkpoint_id())
        # Resolved from the state (NOT step_node's closure local — that
        # scoping NameError'd this lane's first request; the degraded
        # path must always answer).
        agent_prompts = dict(state.get("agent_prompts") or {})
        try:
            base = render_template(
                state.get("system") or DEFAULT_SYSTEM_PROMPT,
                template_args(state.get("data")))
            agent = DeepAgent(
                self._provider, self._mcp,
                system=f"{standing_system(agent_prompts)}\n\n{base}",
                model=state.get("model"),
                max_steps=int(state.get("max_steps")
                              or run_limits().max_conversation_steps),
                temperature=state.get("temperature"),
                max_concurrent_subagents=(
                    run_limits().max_concurrent_subagents),
                subagent_timeout=run_limits().subagent_timeout_sec,
                sandbox=(Sandbox() if state.get("sandbox_enabled", True)
                         else None),
                # The ladder's dynamic rung rides the degraded lane too
                # (the plan node resolved the budget even when the plan
                # itself failed — the catalog is the conversation's menu),
                # beside the KG search kit (the schema index survives
                # every lane).
                builtin_tools={
                    **self._prompt_kit(state).builtin_tools(),
                    **KgToolSet(
                        descriptions=dict(state.get("tool_prompts") or {})
                    ).builtin_tools()},
                tool_prompts=dict(state.get("tool_prompts") or {}))
            with bind_request_chain(writer.context_id):
                result = await agent.run(state.get("goal", ""),
                                         data=state.get("data"))
            self._run_ctx.result = result
            await writer.finish(
                "completed", n_llm_calls=result.steps,
                n_tool_calls=len(result.tool_calls),
                answer_chars=len(result.answer))
            return {"answer": result.answer, "steps": result.steps,
                    "n_tool_calls": len(result.tool_calls)}
        except Exception as exc:  # noqa: BLE001 — error-as-state routing
            self._run_ctx.error = exc
            await writer.finish("failed", error=f"{type(exc).__name__}: {exc}")
            return {"run_error": f"{type(exc).__name__}: {exc}"}
        finally:
            end_request_chain(writer.context_id)

    async def output_node(self, state: ThreadState) -> Dict[str, Any]:
        """The graph's FINAL OUTPUT NODE — never model-planned, always
        the graph's LAST node (after finalize settles the thread). Its
        self._output is FIXED by the builder's :class:`GraphOutputSpec`: ONE
        synthesis conversation over the accumulated findings (a fresh
        context, the spec's directive as its charter, stats/kg/prompt
        tools to re-check cited numbers) whose answer persists as the
        spec's context KIND — the custom graph's ``graph-self._output``
        handoff row (what the RESULT graph reads as its input) or the
        result graph's ``final-result`` row (the run's answer
        document, the payload the UI's Result view displays). No
        usable evidence + a run error records the failure reason with
        ZERO LLM; a thread-less state (the degraded lane — the run
        never persisted) records nothing, exactly the old result
        node's contract. A run cancelled mid-graph never reaches this
        node (the UI says the run left no recorded result)."""
        thread_id = state.get("thread_id")
        if not thread_id:
            return {}
        answer = str(state.get("answer") or "")
        run_error = state.get("run_error")
        findings = [f for f in (state.get("findings") or []) if f]
        if not answer and not findings:
            # Nothing to synthesize (a dead run) — the failure reason is
            # the document; no LLM is spent on it. No context (llm=False),
            # like the old result node's bookkeeping.
            writer = NodeRunWriter(self._store, thread_id, self._output.node, llm=False)
            await writer.begin(entry_checkpoint_id())
            try:
                context_id = await self._record_output(
                    thread_id, self._output, answer=None,
                    error=run_error or "run produced no answer",
                    llm_context_id=None)
            except Exception as exc:  # noqa: BLE001 — fail-soft
                logger.warning("self._output context persist failed: %s", exc)
                await writer.finish("failed",
                                    error=f"{type(exc).__name__}: {exc}")
                return {}
            await writer.finish("skipped")
            return {"result_context_id": context_id}
        # The synthesis conversation — the same step flavor (system =
        # standing + base + step addendum; tools = stats/kg/prompt
        # fetch, NO plan kit — nothing is left to rework at an self._output
        # seat; no subagent lane — the seat has no state row to parent
        # forks under). ONE writer, inside the agent (the node==agent
        # contract): its context is the handoff row's chain.
        agent_prompts = dict(state.get("agent_prompts") or {})
        base = render_template(
            state.get("system") or DEFAULT_SYSTEM_PROMPT,
            template_args(state.get("data")))
        step_addendum = (agent_prompts.get(AGENT_PROMPT_STEP_ADDENDUM)
                         or STEP_SYSTEM_ADDENDUM)
        # The DB-editable charter (2026-11-07): the type-3 directive row
        # for THIS graph's seat wins; the spec's constant is the
        # fail-soft fallback (the seed and the constant stay in sync).
        directive = agent_prompts.get(
            AGENT_PROMPT_CUSTOM_OUTPUT_DIRECTIVE
            if self._output.context_kind == "graph-output"
            else AGENT_PROMPT_RESULT_OUTPUT_DIRECTIVE,
        ) or self._output.directive
        prompt_texts = dict(state.get("prompt_texts") or {})
        prompt_catalog = dict(state.get("prompt_catalog") or {})
        node_prompts = await self._node_prompts(state, directive)
        directive_row = StateRow(
            state_id=0, thread_id=thread_id, parent_state_id=None,
            node=self._output.node, title=directive, status="pending",
            ordinal=len(findings) + 1, input_context_id=None,
            result_context_id=None, error=None)
        limits = run_limits()
        message = step_user_message(StepPromptContext(
            goal=state.get("goal", ""), data=state.get("data"),
            state=directive_row, findings=findings,
            prompt_texts=prompt_texts,
            prompt_catalog=prompt_catalog,
            node_prompts=node_prompts,
            digest=state.get("findings_digest"),
            n_compacted=int(state.get("n_compacted") or 0)))
        deps = OutputDeps(
            build_message=lambda: message,
            make_tools=lambda: {
                **StatsToolSet(
                    descriptions=dict(state.get("tool_prompts") or {})
                ).builtin_tools(),
                **KgToolSet(
                    descriptions=dict(state.get("tool_prompts") or {})
                ).builtin_tools(),
                **self._prompt_kit(state).builtin_tools(),
            },
            sandbox=lambda: self.run_sandbox(state),
            system=(f"{standing_system(agent_prompts)}\n\n"
                    f"{base}\n\n{step_addendum}"),
            common=dict(
                model=state.get("model"),
                max_steps=max(limits.min_conversation_steps, min(
                    int(state.get("max_steps")
                        or limits.max_conversation_steps),
                    limits.max_steps_cap)),
                temperature=state.get("temperature"),
            ),
            mcp=self._mcp,
        )
        agent = OutputAgent(
            self._provider, deps=deps, thread_id=thread_id,
            model=state.get("model"),
            writer_factory=lambda: NodeRunWriter(
                self._store, thread_id, self._output.node, model=state.get("model")))
        synthesis, n_llm, n_tool = await agent.run()
        failed = synthesis.startswith("OUTPUT FAILED")
        try:
            context_id = await self._record_output(
                thread_id, self._output,
                answer=None if failed else synthesis,
                error=run_error if failed else None,
                llm_context_id=agent.last_context_id)
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("self._output context persist failed: %s", exc)
            context_id = None
        update: Dict[str, Any] = {
            "steps": int(state.get("steps") or 0) + n_llm,
            "n_tool_calls": int(state.get("n_tool_calls") or 0) + n_tool,
            "result_context_id": context_id,
        }
        if not failed and synthesis:
            update["answer"] = " ".join(synthesis.split())
        return update

    async def finalize_node(self, state: ThreadState) -> Dict[str, Any]:
        thread_id = state.get("thread_id")
        if not thread_id:
            return {}
        # No LLM work here — llm=False: no context, no registry item.
        writer = NodeRunWriter(self._store, thread_id, NODE_FINALIZE, llm=False)
        await writer.begin(entry_checkpoint_id())
        try:
            snapshot = await PLAN_STORE.settle_chain(
                thread_id, failure=state.get("run_error"))
        except Exception as exc:  # noqa: BLE001 — best-effort settle
            logger.warning("thread finalize failed: %s", exc)
            await writer.finish("failed", error=f"{type(exc).__name__}: {exc}")
            return {}
        await writer.finish("completed", n_states=len(snapshot))
        # The settled snapshot never rides the state — run_thread
        # re-reads the plan_store after the graph completes (plain-value
        # checkpoints; a failed settle still leaves the rows readable
        # there, where the old state key would have gone empty).
        return {}

    def route_after_node(self, state: ThreadState) -> str:
        return str(state.get("next") or "finalize")

    def route_entry(self, state: ThreadState) -> str:
        """The graph's entry: the orchestrator's initial state carries
        the entry flag — the step lane when the graph's steps exist
        (always true for the result graph), the degraded execute lane
        when the custom graph's plan failed (self._degraded_lane builds
        only); a lane-less build (the result graph) with a False flag
        routes straight to finalize — the wave is skipped, the self._output
        node still records whatever the chain produced."""
        if state.get("plan_ok", True):
            return "step"
        return "execute" if self._degraded_lane else "finalize"

    async def _record_output(
        self,
        thread_id: str,
        spec: GraphOutputSpec,
        *,
        answer: Optional[str],
        error: Optional[str],
        llm_context_id: Optional[str],
    ) -> Optional[int]:
        """Persist one output node's document by the spec's context KIND —
        the final-result row (the run's answer, keyed by kind exactly as
        the UI's Result view reads it) or the graph-output handoff row
        (the custom graph's synthesis, chained to its conversation)."""
        if spec.context_kind == "final-result":
            return await PLAN_STORE.record_final_result(
                thread_id, answer=answer, error=error)
        return await PLAN_STORE.record_graph_output(
            thread_id, answer, node=spec.node, error=error,
            llm_context_id=llm_context_id)

    def build(self):
        builder = StateGraph(ThreadState)

        builder.add_node("step", self.step_node)

        builder.add_node("compact", self.compact_node)

        if self._degraded_lane:
            builder.add_node("execute", self.execute_node)

        builder.add_node("finalize", self.finalize_node)

        builder.add_node("output", self.output_node)

        if self._degraded_lane:
            builder.add_conditional_edges(
                START, self.route_entry,
                {"step": "step", "execute": "execute",
                 "finalize": "finalize"})
        else:
            builder.add_conditional_edges(
                START, self.route_entry,
                {"step": "step", "finalize": "finalize"})

        builder.add_conditional_edges(
            "step", self.route_after_node,
            {"step": "step", "compact": "compact", "finalize": "finalize"})

        builder.add_conditional_edges(
            "compact", self.route_after_node,
            {"step": "step", "finalize": "finalize"})

        if self._degraded_lane:
            builder.add_edge("execute", "finalize")

        builder.add_edge("finalize", "output")

        builder.add_edge("output", END)

        return builder.compile(checkpointer=self._checkpointer,
                               store=self._store)

