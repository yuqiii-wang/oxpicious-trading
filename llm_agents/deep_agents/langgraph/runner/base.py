"""llm_agents.deep_agents.langgraph.runner.base — the ExecutionGraphBuilder's per-run BASE: the ctor
captures, the shared sandbox cell and the DYNAMIC rung of the
prompt-loading ladder (catalog rows + raw-instruction cache) —
the step/node methods live in the ``step_lane`` / ``nodes``
mixin modules.

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

logger = logging.getLogger("deep_agents.runner.base")

from llm_agents.deep_agents.langgraph.runner.state import (  # noqa: E402
    CUSTOM_OUTPUT_SPEC, GraphOutputSpec, RESULT_OUTPUT_SPEC,
    ThreadState, _RunContext,
)


class ExecutionGraphBuilderBase:
    """ONE EXECUTION GRAPH's builder (2026-11-06 — the former
    ``build_execution_graph`` closure factory): the per-run captures
    (provider, mcp, run context, store, repair channel, sandbox cell,
    the output charter) are the object's fields and the graph's nodes
    are its methods — the step lane, the compact detour, the execute
    degrade, the output seat, finalize and the two routers. The nested
    helpers INSIDE :meth:`step_node` stay nested: they capture the
    hop's OWN locals (the same semantics the closures had).
    Compile ONE EXECUTION GRAPH of the three-graph chain (the
custom graph over the plan's steps; the result graph over the
review wave): entry → (steps as nodes | compact) → finalize →
output, with the deep-agent store's node states / context registry
(the checkpointer is gone — 2026-11-06; the *checkpointer* kwarg
stays accepted as None for call-site compatibility). The step lane
is ONE looping node — each hop is its own superstep executing ONE
planned step, so the plan the plan session wrote literally is the
node chain LangGraph walks, and the router between hops re-reads
the living plan tree (replans steer the next hop; when the
accumulated findings approach the LLM context limit the router
detours through the compact node first).

*output* is the graph's FINAL OUTPUT NODE charter (GraphOutputSpec)
— the custom graph's seat synthesizes the findings into the
graph-output handoff row; the result graph's seat writes the run's
final-result document. *degraded_lane* registers the plain
execute node behind the entry router (the custom graph, whose
plan may have failed; the result graph — whose lane is fixed —
routes a False plan_ok straight to finalize instead).
*sandbox_cell* shares ONE sandbox session across the run's graphs
(the orchestrator mints the cell once; a standalone build gets its
own). NO supervisor node: the watchdog is independent of the
graphs — the supervisor SESSION (the backend poller) runs beside
the whole CHAIN in the orchestrator; *repair* is the lane↔poller
channel whose pause window the step lane consults on a root-path
failure. The live agent objects are closed over by the nodes
(never carried in state — see the STATE SERIALIZABILITY note in the
module docstring).
    """

    def __init__(
        self,
        provider: ZhipuChatProvider,
        mcp: McpToolClient,
        run_ctx: _RunContext,
        *,
        checkpointer: Any = None,
        store: Optional[BaseStore] = None,
        repair: Optional[RepairChannel] = None,
        sandbox_cell: Optional[List[Any]] = None,
        output: GraphOutputSpec = CUSTOM_OUTPUT_SPEC,
        degraded_lane: bool = True,
    ) -> None:
        self._provider = provider
        self._mcp = mcp
        self._run_ctx = run_ctx
        # The checkpointer is GONE (2026-11-06) — accepted as None for
        # call-site compatibility only.
        self._checkpointer = checkpointer
        self._store = store
        self._repair = repair
        self._output = output
        self._degraded_lane = degraded_lane
        self._sandbox_cell = sandbox_cell
        # ONE sandbox session per RUN shared by every hop of every
        # graph (the sandbox mints a remote session on its first tool
        # call; without this cell each step would start an empty one
        # and lose the earlier steps' files).
        if self._sandbox_cell is None:
            self._sandbox_cell = [None]


    def run_sandbox(self, state: ThreadState) -> Optional["Sandbox"]:
        if self._sandbox_cell[0] is None and state.get("sandbox_enabled", True):
            self._sandbox_cell[0] = Sandbox()
        return self._sandbox_cell[0]

    async def _catalog_rows(self, state: ThreadState) -> List[CatalogRow]:
        if not self._catalog_rows_cell[0]:
            catalog, _matches = await PlanSession.fetch_prompt_catalog_ladder(
                list(state.get("prompt_keys") or []), state.get("data"),
                template_args(state.get("data")))
            self._catalog_rows_cell[0] = catalog
        return self._catalog_rows_cell[0]

    async def _prompt_raw(self, key: str) -> Optional[str]:
        if key in self._prompt_text_cache:
            return self._prompt_text_cache[key] or None
        try:
            rows = await PLAN_STORE.fetch_prompt_instructions([key])
        except Exception as exc:  # noqa: BLE001 — dynamic rung is fail-soft
            logger.warning("dynamic prompt fetch failed for %s: %s", key, exc)
            return None
        raw = rows[0].instruction if rows else ""
        self._prompt_text_cache[key] = raw
        return raw or None

    async def _node_prompts(self, 
            state: ThreadState, directive: str) -> Dict[str, str]:
        """THIS node's keyword-matched prompts, loaded ahead of its
        conversation (the higher-priority rung: no model judgment —
        bound/optional label hits on the node's own directive, or a
        study prompt key the directive NAMES, e.g. a sub-plan goal).
        Only the run's NOT-YET-LOADED rows enter (already-matched and
        loaded ones ride every message already); fail-soft to {}."""
        catalog = dict(state.get("prompt_catalog") or {})
        if not catalog or not str(directive).strip():
            return {}
        try:
            rows = await self._catalog_rows(state)
            data = state.get("data")
            args = template_args(data)
            unloaded = [r for r in rows if r.prompt_key in catalog]
            matches = PromptLadder(unloaded).node_matches(
                str(directive),
                scope_name=args.get("name", ""),
                item_labels=item_labels(data),
                prompt_labels=(data or {}).get("prompt_labels")
                if isinstance((data or {}).get("prompt_labels"), dict)
                else None)
            loaded: Dict[str, str] = {}
            for key in matches:
                raw = await self._prompt_raw(key)
                if raw:
                    loaded[key] = render_template(raw, args)
            return loaded
        except Exception as exc:  # noqa: BLE001 — degrade, no node loads
            logger.warning("node prompt matching failed: %s", exc)
            return {}

    def _prompt_kit(self, state: ThreadState) -> PromptLoadToolKit:
        """The load_prompt kit over the run's CURRENT catalog (the
        loader renders the run's {{key}} template args — a dynamically
        loaded text reads exactly like a plan-time loaded one)."""
        args = template_args(state.get("data"))

        async def _loader(key: str) -> Optional[str]:
            raw = await self._prompt_raw(key)
            return render_template(raw, args) if raw else None

        return PromptLoadToolKit(
            dict(state.get("prompt_catalog") or {}), _loader,
            descriptions=dict(state.get("tool_prompts") or {}))

