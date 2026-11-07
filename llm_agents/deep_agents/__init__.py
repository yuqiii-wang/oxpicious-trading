"""llm_agents.deep_agents — a plan-as-graph research agent harness.

One deep-agent run studies a business ask, lays out research steps, and
executes them as a LangGraph node chain — each step its own
conversation, forks delegated to supervised subagents, the whole living
plan observable and mutable while it runs.

ARCHITECTURE — the layering follows what the modern agent harnesses
converged on (studied while designing this split): Claude Code's ONE
plain agent loop with capabilities as layers around it (tools / hooks /
subagents / MCP); DeepSeek Harness's "everything is a plugin" runtime
(models, tools, skills, sessions, loops & scheduling all replaceable
around the loop, with an append-only session log for replay); WorkBuddy
(Tencent)'s multi-agent workspace (specialized agents per task under
one orchestrator). The common capabilities every one of them carries —
a plain loop, replaceable tool/model transports, delegation with
parent-owned supervision, first-class planning, and a session/trajectory
record — each live in exactly one layer here:

  core/      the RUNTIME — the loop and its capabilities
               loop (DeepAgent: one message list, one provider call per
               step, until a no-tool reply; sync + background-fork
               spawning, collect, answer-gating, no-orphan cleanup),
               delegation (the Task-tool contract + supervision hooks),
               provider + mcp (the model and tool transports),
               sandbox (the code-execution client),
               tools (user-message assembly + the BuiltinTool
               re-export).
  tools/     the TOOL LAYER — every model-callable capability (beside
               the external MCP tools) as an OBJECT: a Tool subclass
               per tool (schema + run handler + its llm.ai_prompts
               tool row — prompt_type 4, seeded from the classes'
               DEFAULT_DESCRIPTION constants and overriding them
               fail-soft per run), grouped into ToolKits the runner
               mounts per conversation role (sandbox_tools /
               plan_tools / thread_search / delegation_tools; the
               spawn/collect handlers stay loop methods — their
               supervision state lives in the loop).
  skills/    the SKILL LAYER — the run's selected study instructions
               (llm.ai_prompts rows) as objects: one Skill per row,
               one SkillBook per run (the LOADED set rendered as the
               pass-down every step conversation and fork receives;
               the quoted selected-prompts block builder). WHICH rows
               load is the GRADUAL LOADING LADDER's decision
               (planning/prompt_loading: catalog previews → keyword
               match → the planner's load_prompts verdict past the
               eager budget — the rest ride as per-step catalog lines).
  planning/  the LIVING-PLAN DOMAIN — the plan-JSON contract
               (validation + repair), the plan tree persistence
               (llm.deep_agent_states + contexts + identities), and
               the mutation AUTHORITIES (the main lane reworks
               not-yet-run steps and terminates forks; a subagent is
               structurally confined to its own forked path — the
               model-facing tools over an authority live in tools/).
  agents/ the AGENT LAYER (2026-11-06) — ONE agent == ONE graph node:
               NodeAgent (agents/base.py) owns a node's identity
               (thread id / node name / state row) and its ONE chained
               LLM context (injected writer + request chain), with
               resume() as the supervisor-repair entry. PlanAgent
               (planning pre-graph), SupervisorAgent (beside the graph;
               interventions are first-class context rows now),
               StepAgent (inline hops; a retry verdict carries the
               supervisor's INSTRUCTION and the agent resumes with its
               prior context — instruction + prior error + prior chain
               ref via search_thread_context — instead of cold).
  planning/session.py the PLAN ENGINE — the pure planning domain
               (batched proposal + prompt ladder + payload helpers);
               the planning LIFECYCLE lives in agents/plan_agent.py
               (planning OUTSIDE the graph since 2026-11-06: no plan
               node, no checkpoint, no state round-trip; the ladder
               outcome rides the graph's INITIAL state).
  langgraph/ the ORCHESTRATION — the plan compiled as the LangGraph
               node chain (entry router → one node hop per step →
               finalize → result, routed hop-by-hop off the fresh plan
               tree; the plan agent runs BEFORE the graph, the
               supervisor session BESIDE it),
               beside which runs the SUPERVISOR WATCHDOG
               (supervisor/ — its OWN package since 2026-11-05,
               outside langgraph: an independent per-run agent, NOT a
               graph node — idle on healthy runs, woken by a pure code
               filter on settled node states only when a root-path
               node failed; a BOUNDED tool-carrying intervention (its
               own SupervisorToolKit: search_thread_context +
               kg_search over a mini tool-loop, its own self-fetched
               system prompt) diagnoses the error and recommends
               whether the plan/topology needs an update — observe
               mode, recorded, never applied; the runner injects the
               NodeRunWriter factory, so only langgraph composes), the
               prompt surfaces (business framing, per-step directives,
               the findings bridge between step conversations), the
               delegation hooks + fork broker, and the deep-agent
               LangGraph store (one node one LLM context; states and
               context references persisted, message data never).

Entrypoints (the runtime "modes", in DeepSeek Harness's sense of
presets over the same core):

  server.py  the always-on FastAPI daemon (``python -m
             llm_agents.deep_agents.server`` — the watchdog/heartbeat
             contract, the data_viz API bridge's transport);
  cli.py     the one-shot CLI (``python -m llm_agents.deep_agents`` —
             plain loop, no thread graph).

Layering rule: core knows nothing of planning/langgraph (it consumes tool
DEFINITIONS from the tools/ leaf submodules only); planning knows
nothing of langgraph/core (one documented lazy exception: the purge
helper deletes the store items langgraph owns); skills sits on planning
(template machinery) and nothing imports it back; tools wraps planning
(plan tools over an authority) and core (the sandbox client, the
BuiltinTool pair); only langgraph composes them, and only the
entrypoints compose langgraph. No cudf/pandas in this package: the
agents move prompts, tool schemas, and JSON-able results only — DB
reads happen inside the MCP server process.
"""
from llm_agents.deep_agents.core.delegation import (
    SubagentRequest,
    SubagentRun,
    SubagentSpawnRejected,
    format_subagent_result,
)
from llm_agents.deep_agents.core.loop import AgentResult, DeepAgent
from llm_agents.deep_agents.core.mcp import McpToolClient
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.core.sandbox import Sandbox
from llm_agents.deep_agents.skills.base import Skill, SkillBook
from llm_agents.deep_agents.tools.base import Tool, ToolKit

__all__ = [
    "AgentResult", "DeepAgent", "McpToolClient", "Sandbox",
    "Skill", "SkillBook", "SubagentRequest", "SubagentRun",
    "SubagentSpawnRejected", "Tool", "ToolKit", "ZhipuChatProvider",
    "format_subagent_result",
]
