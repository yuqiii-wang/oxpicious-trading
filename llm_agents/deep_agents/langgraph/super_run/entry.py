"""llm_agents.deep_agents.langgraph.super_run.entry — the module entry point: ``run_thread`` — the thin free
function the server/CLI/probes call (constructs the runner and
drives it).
"""
from __future__ import annotations

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from langgraph.store.base import BaseStore

from llm_agents.deep_agents.core.mcp import McpToolClient
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.core.loop import AgentResult
from llm_agents.deep_agents.langgraph.runner import (
    CUSTOM_OUTPUT_SPEC, RESULT_OUTPUT_SPEC, _RunContext,
    build_execution_graph, build_plan_graph, ThreadRunResult,
)
from llm_agents.deep_agents.langgraph.prompts import finding_line
from llm_agents.deep_agents.langgraph.state_store import (
    NODE_PLAN, NODE_SUPERVISOR, DeepAgentStore, NodeRunWriter,
    settle_node_items,
)
from llm_agents.deep_agents.agents import PlanAgent, SupervisorAgent
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.plan_json import review_wave_steps
from llm_agents.deep_agents.planning.session import (
    PlanSessionResult, plan_payload_steps,
)
from llm_agents.deep_agents.planning.store import (StateRow, identities_from_scope, PLAN_STORE)
from llm_agents.deep_agents.planning.templates import (
    TEMPLATE_LIBRARY,
)
from llm_agents.deep_agents.supervisor.repair import RepairChannel

logger = logging.getLogger("deep_agents.langgraph.super_run")
from llm_agents.deep_agents.langgraph.super_run.chain import (  # noqa: E402
    SuperGraphRunner, STOP_FAILURE,
)


async def run_thread(
    provider: ZhipuChatProvider,
    mcp: McpToolClient,
    *,
    goal: str,
    data: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    max_steps: Optional[int] = None,
    system: Optional[str] = None,
    temperature: Optional[float] = None,
    enable_sandbox: bool = True,
    thread_id: Optional[str] = None,
    plan_template_id: Optional[int] = None,
) -> ThreadRunResult:
    """One SUPER RUN — three chained LangGraphs (plan → custom →
    result), three threads, one ``super_thread_id`` (see the module
    docstring and :class:`SuperGraphRunner`). Same signature as the
    pre-refactor entry: *thread_id* is the chain's ROOT (the plan
    thread); the response's plan is the settled CHAIN snapshot;
    ``plan_template_id`` starts the run from a SAVED PLAN TEMPLATE
    (a type-plan row of llm.deep_agent_langgraph_templates — the
    materialized steps skip the planning session, fail-soft to the
    LLM proposal); ``enable_sandbox`` mints the run's ONE code-sandbox
    scratch directory shared by every graph."""
    return await SuperGraphRunner(
        provider, mcp,
        goal=goal, data=data, model=model, max_steps=max_steps,
        system=system, temperature=temperature,
        enable_sandbox=enable_sandbox, thread_id=thread_id,
        plan_template_id=plan_template_id,
    ).run()

