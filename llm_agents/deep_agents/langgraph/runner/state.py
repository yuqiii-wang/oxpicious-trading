"""llm_agents.deep_agents.langgraph.runner.state — the graph STATE side of the three-graph chain: the

STATE SERIALIZABILITY: every ``ThreadState`` value is PLAIN JSON-ish —

no custom type rides a checkpoint. The plan itself never enters the

state: the plan_store DB rows own the living tree and every hop

re-reads it fresh (a checkpointed plan copy would go stale the moment

update_plan mutates the tree), and the settled snapshot finalize

produces is re-read from the plan_store by ``run_thread`` after the

graph completes. The live agent objects (provider

/ MCP client / asyncio tasks) are closed over by the nodes, never

carried in state. The tool-call TRANSCRIPT is state-external too: step

hops return counts (``n_tool_calls``) and the run context carries the

transcripts, so checkpoints and store items stay STATES — LLM context

data is referenced by id, never persisted.


(The full orchestration docstring lives in
the package ``__init__``; the split, 2026-11-07, kept every class
and function body byte-identical.)
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

from llm_agents.deep_agents.langgraph.runner.data_definitions import (
    GraphOutputSpec,
    ThreadRunResult,
    _RunContext,
)
logger = logging.getLogger("deep_agents.runner.state")


class ThreadState(TypedDict, total=False):
    """The graph's shared state — every value serializable (they ride
    the checkpointer's checkpoints). Nodes return partial updates;
    LangGraph merges per key (last write wins — no reducers needed for
    a linear graph)."""

    goal: str
    data: Optional[Dict[str, Any]]
    prompt_keys: List[str]
    # The run's TOTAL wall-clock anchor (time.monotonic() at the graph
    # invoke) — the step lane's per-hop deadline check routes the chain
    # to finalize at run_limits().run_timeout_sec instead of grinding
    # forever. Plain float, checkpoint-safe.
    run_started: float
    # The LOADED prompts RENDERED (prompt_key -> instruction text,
    # {{key}} args substituted) — the gradual loading ladder's outcome
    # (plan rows + eager/forced/verdict skills; see
    # planning.prompt_loading), resolved AT THE PLAN NODE (the verdict
    # exists only after the proposal) and passed down to every step
    # hop's conversation and every forked child's input. Plain dict of
    # str, so it rides the checkpointer fine.
    prompt_texts: Dict[str, str]
    # The NOT-loaded selected rows as catalog lines (prompt_key -> one
    # preview line) — the per-step fetch menu: a step working a
    # cataloged skill's domain fetches THAT ONE row by key through its
    # DB tools instead of the wholesale skill fetch. Plain dict of str,
    # checkpoint-safe like prompt_texts.
    prompt_catalog: Dict[str, str]
    # THE SUPERVISOR REPAIR LANE's instructed-resume map (2026-11-06):
    # str(state_id) -> {"instruction": ..., "prior_error": ...} — the
    # retry verdicts' supervisor instructions. The repair window writes
    # entries; the re-picked step's StepAgent consumes its entry and
    # runs in resume mode (the instruction + prior error + the prior
    # attempt's chain ref ride its step message). str keys + plain
    # str values, checkpoint-safe.
    resume_instructions: Dict[str, Dict[str, str]]
    # The run-start resolution of the agent's INTERNAL prompt rows
    # (llm.ai_prompts prompt_type 3 — seeded from the inline constants
    # by static/ai_prompts/01_deep_agent_prompts.sql): prompt_key ->
    # instruction. A key missing here = that consumer's built-in
    # constant (fail-soft sourcing; the constants stay the fallback by
    # design).
    agent_prompts: Dict[str, str]
    # The run-start resolution of the TOOL-description rows
    # (llm.ai_prompts prompt_type 4 — seeded from the tool classes by
    # static/ai_prompts/10_tool_prompts.sql): "prompt_key|sub_type" ->
    # instruction, applied by the tool kits at mount time (tools.base's
    # override contract; a missing key = the tool's built-in
    # description). Plain str keys, so it rides the checkpointer.
    tool_prompts: Dict[str, str]
    model: Optional[str]
    max_steps: int
    system: Optional[str]
    temperature: Optional[float]
    # False removes the sandbox_* builtin tools from the execute node's
    # agent (the sandbox is ON by default — the runner mints one scratch
    # directory per run when this is True).
    sandbox_enabled: bool
    # At INPUT: the run-level thread id (the checkpointer's key) the
    # plan node reuses for create_thread. After the plan node: the
    # persisted plan's thread id, or None (degraded / no plan prompts —
    # the response contract: no thread unless the plan phase persisted).
    thread_id: Optional[str]
    # The RUNNING PLANNING NODE's state row (begin_thread — the ordinal-0
    # 'plan' row that exists from run start; the plan node stamps its
    # context onto it and closes it). None when that insert degraded.
    # (2026-11-06: plan_state_id / plan_template_id left the state —
    # the plan agent session runs OUTSIDE the graph)
    # A SAVED PLAN TEMPLATE to plan from instead of the LLM
    # (llm.deep_agent_plan_templates template_id — the Thread Graph
    # Save button's placeholder-templated plan): the plan node's
    # template lane renders it with the run's input args and the plan
    # finishes with ZERO planning LLM calls. None (the default) = the
    # normal proposal session. Plain int, so it rides checkpoints fine.
    # The PLAN itself never rides the state: the plan_store DB rows own
    # the living tree and every hop re-reads it fresh — a checkpointed
    # plan copy would go stale the moment update_plan mutates the tree
    # (and custom types in checkpoints need msgpack allowlisting). All
    # state values stay plain JSON-ish; see STATE SERIALIZABILITY in the
    # module docstring.
    # Non-None when a node failed fatally (the degraded execute lane):
    # finalize settles the thread with this failure reason; the ORIGINAL
    # exception rides the run context and run_thread re-raises it after
    # the graph completes. A single step hop's failure does NOT set
    # this — the chain advances and the run survives on the remaining
    # steps' findings.
    run_error: Optional[str]
    answer: str
    steps: int
    # COUNTS only — the tool-call transcript is LLM context data: it
    # rides the run context side channel, never the persisted state
    # (checkpoints and store items stay states; contexts referenced).
    # Accumulated across step hops (each hop returns the running total).
    n_tool_calls: int
    # The per-step evidence the step conversations meet through: one
    # bounded plain string per completed/failed hop ("<ordinal>. <title>
    # — <finding>"). Plain strings keep the checkpointer happy.
    findings: List[str]
    # CONTEXT COMPACTION (langgraph.compaction): the compact node's merged
    # digest of the findings compacted so far (None until the first
    # compaction) and how many raw finding lines the digest covers. A
    # finished step hop still appends to ``findings`` — the tail rides
    # BESIDE the digest until the next compact node folds it in (the
    # merge cycle: finished nodes merge into the latest compacted node,
    # never silently dropped).
    findings_digest: Optional[str]
    n_compacted: int
    # The router's next hop ("step" | "execute" | "compact" |
    # "finalize") — each node computes it from the FRESH plan tree as
    # it finishes, so living-plan mutations steer the very next hop.
    # Nothing routes to the supervisor: it is not a graph node (the
    # watchdog lives in run_thread's tail, outside the graph).
    next: str
    # The result node's fixed output: the context id of its kind
    # final-result row (the run's answer document — what the UI's Result
    # view displays). None until the result node ran (or it recorded
    # nothing: no answer and no error).
    result_context_id: Optional[int]




CUSTOM_OUTPUT_SPEC = GraphOutputSpec(
    node=NODE_OUTPUT, context_kind="graph-output",
    directive=CUSTOM_OUTPUT_DIRECTIVE)
# The result graph's seat — the run's final answer document.
RESULT_OUTPUT_SPEC = GraphOutputSpec(
    node=NODE_OUTPUT, context_kind="final-result",
    directive=RESULT_OUTPUT_DIRECTIVE)
