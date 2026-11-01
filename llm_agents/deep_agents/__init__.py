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
               tools (BuiltinTool plumbing), provider + mcp (the model
               and tool transports), sandbox (code execution).
  planning/  the LIVING-PLAN DOMAIN — the plan-JSON contract
               (validation + repair), the plan tree persistence
               (text.deep_agent_states + contexts + identities), and
               the mutation AUTHORITIES (the main lane reworks
               not-yet-run steps and terminates forks; a subagent is
               structurally confined to its own forked path).
  graph/     the ORCHESTRATION — the plan compiled as the LangGraph
               node chain (plan → one node hop per step → finalize →
               result, routed hop-by-hop off the fresh plan tree), the
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

Layering rule: core knows nothing of planning/graph; planning knows
nothing of graph/core (one documented lazy exception: the purge helper
deletes the store items graph owns); only graph composes them, and
only the entrypoints compose graph. No cudf/pandas in this package:
the agents move prompts, tool schemas, and JSON-able results only —
DB reads happen inside the MCP server process.
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

__all__ = [
    "AgentResult", "DeepAgent", "McpToolClient", "Sandbox",
    "SubagentRequest", "SubagentRun", "SubagentSpawnRejected",
    "ZhipuChatProvider", "format_subagent_result",
]
