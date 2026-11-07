"""llm_agents.deep_agents.langgraph.runner.plan_builder — the chain's FIRST graph: PlanGraphBuilder (the
plan session as a two-node StateGraph) + the thin
``build_plan_graph`` entry point.

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

logger = logging.getLogger("deep_agents.runner.plan_builder")

from llm_agents.deep_agents.langgraph.runner.state import (  # noqa: E402
    CUSTOM_OUTPUT_SPEC, GraphOutputSpec, RESULT_OUTPUT_SPEC,
    ThreadState, _RunContext,
)


class PlanGraphBuilder:
    """The chain's FIRST graph's builder (2026-11-07 — the former
    ``build_plan_graph`` closure factory): the plan session as a
    two-node StateGraph — ``plan`` (the PlanAgent's batched proposal +
    streaming persistence) -> ``output`` (the FINAL OUTPUT NODE: the
    complete plan payload landed over the first batch's partial row —
    the plan context row IS this graph's output artifact, what the
    custom graph is built from) -> END. The plan session's full result
    rides the run context side channel (its ThreadPlan/payload are not
    plain state values); the state carries only the plain entry flag
    and counts."""

    def __init__(
        self,
        provider: ZhipuChatProvider,
        mcp: McpToolClient,
        run_ctx: _RunContext,
        *,
        store: Optional[BaseStore] = None,
        plan_agent: PlanAgent,
        plan_state_id: Optional[int] = None,
        plan_template_id: Optional[int] = None,
        prompt_keys: Optional[List[str]] = None,
        agent_prompts: Optional[Dict[str, str]] = None,
    ) -> None:
        self._provider = provider
        self._mcp = mcp
        self._run_ctx = run_ctx
        self._store = store
        self._plan_agent = plan_agent
        self._plan_state_id = plan_state_id
        self._plan_template_id = plan_template_id
        self._prompt_keys = list(prompt_keys or [])
        self._agent_prompts = dict(agent_prompts or {})

    async def plan_node(self, state: ThreadState) -> Dict[str, Any]:
        result = await self._plan_agent.run(
            goal=state.get("goal", ""),
            data=state.get("data"),
            prompt_keys=self._prompt_keys,
            agent_prompts=self._agent_prompts,
            plan_state_id=self._plan_state_id,
            plan_template_id=self._plan_template_id)
        self._run_ctx.plan_result = result
        return {
            "plan_ok": result.plan_ok,
            "steps": int(result.n_calls),
        }

    async def output_node(self, state: ThreadState) -> Dict[str, Any]:
        """The PLAN graph's final output node: land the COMPLETE plan
        payload (the session returned it — ladder outcome, provenance,
        every persisted batch's steps) over the first batch's partial
        plan context row. Fail-soft: a failed land leaves the partial
        row standing (the run proceeds on the persisted step rows)."""
        result = self._run_ctx.plan_result
        if result is None or result.plan_payload is None:
            return {}
        thread_id = result.thread_id
        if not thread_id:
            return {}
        try:
            await PLAN_STORE.replace_plan_payload(
                thread_id, result.plan_payload)
        except Exception as exc:  # noqa: BLE001 — partial row stands
            logger.warning("plan payload land failed (%s) — the partial "
                           "plan context row stands", exc)
        return {}

    def build(self):
        builder = StateGraph(ThreadState)
        builder.add_node(NODE_PLAN, self.plan_node)
        builder.add_node(NODE_OUTPUT, self.output_node)
        builder.add_edge(START, NODE_PLAN)
        builder.add_edge(NODE_PLAN, NODE_OUTPUT)
        builder.add_edge(NODE_OUTPUT, END)
        return builder.compile(store=self._store)


def build_plan_graph(
    provider: ZhipuChatProvider,
    mcp: McpToolClient,
    run_ctx: _RunContext,
    *,
    store: Optional[BaseStore] = None,
    plan_agent: PlanAgent,
    plan_state_id: Optional[int] = None,
    plan_template_id: Optional[int] = None,
    prompt_keys: Optional[List[str]] = None,
    agent_prompts: Optional[Dict[str, str]] = None,
):
    """Compile the chain's FIRST graph — :class:`PlanGraphBuilder`'s
    :meth:`build` (the former closure factory, 2026-11-07)."""
    return PlanGraphBuilder(
        provider, mcp, run_ctx, store=store, plan_agent=plan_agent,
        plan_state_id=plan_state_id, plan_template_id=plan_template_id,
        prompt_keys=prompt_keys, agent_prompts=agent_prompts).build()

