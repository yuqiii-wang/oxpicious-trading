"""llm_agents.deep_agents.tools — the TOOL LAYER: every model-callable
capability (beside the external MCP tools) as an OBJECT.

One :class:`Tool` subclass per tool — schema, ``run`` handler, and its
``llm.ai_prompts`` row (prompt_type 4: seeded from the classes'
DEFAULT_DESCRIPTION constants by
``database/sql/text/llm/static/ai_prompts/10_tool_prompts.sql``, overriding
them fail-soft per run) — grouped into :class:`ToolKit` s the runner
mounts per conversation role:

  base.py             — Tool / ToolKit / BuiltinTool (the loop's
                        runtime pair; core.tools re-exports it);
  sandbox_tools.py    — the four sandbox_* tools over one Sandbox
                        client (SandboxToolSet);
  plan_tools/         — the plan-mutation tools over a PlanAuthority
                        (MainPlanToolKit / ForkPlanToolKit + the fork
                        addenda; package: addenda / common / tick /
                        update_plan / kits);
  stats_tools/        — the six stats_* study tools over the daily
                        bars (StatsToolSet: ma/rsi/macd/roc/volume/
                        range — plain numpy, read replica);
  kg_tools/           — the knowledge-graph kit as THIN ADAPTERS over
                        the independent llm_agents.kg module
                        (KgToolSet: kg_search traversal search +
                        kg_expand neighborhood — schema + argument
                        coercion + agent-flow hints; the graph load,
                        the two-layer chain cache and the renders live
                        domain-side, on the process-wide
                        KnowledgeGraphService);
  thread_search.py    — the thread-internal traffic search
                        (ThreadSearchTool);
  prompt_tools.py     — load_prompt, the prompt-loading ladder's
                        dynamic rung (PromptLoadToolKit — one
                        cataloged skill's instruction on demand);
  delegation_tools.py — spawn/collect_subagents schema objects (their
                        HANDLERS are loop methods — the supervision
                        state they need lives in the loop).

BOUNDARY — who imports what:

  * ``core`` consumes tool definitions via the leaf submodules
    (``tools.base``, ``tools.sandbox_tools``,
    ``tools.delegation_tools``) — never through plan_tools, which
    pulls ``planning``;
  * ``planning`` never imports this package (the search body and the
    permission model stay planning-side; this layer wraps them);
  * ``graph`` composes: the runner mounts SandboxToolSet +
    MainPlanToolKit + StatsToolSet + KgToolSet, the delegation hooks
    stamp ForkPlanToolKit + StatsToolSet + KgToolSet.

This package root re-exports the layer contract plus the delegation
schemas — deliberately NOT plan_tools (its planning import would drag
the plan domain into every importer).
"""
from llm_agents.deep_agents.tools.base import BuiltinTool, Tool, ToolKit
from llm_agents.deep_agents.tools.delegation_tools import (
    COLLECT_SUBAGENTS_SCHEMA, COLLECT_TOOL_NAME, CollectSubagentsTool,
    SUBAGENT_TOOL_NAME, SUBAGENT_TOOL_SCHEMA, SpawnSubagentTool,
)
from llm_agents.deep_agents.tools.prompt_tools import (
    LoadPromptTool, PromptLoadToolKit,
)

__all__ = [
    "BuiltinTool", "CollectSubagentsTool", "COLLECT_SUBAGENTS_SCHEMA",
    "COLLECT_TOOL_NAME", "LoadPromptTool", "PromptLoadToolKit",
    "SpawnSubagentTool", "SUBAGENT_TOOL_NAME",
    "SUBAGENT_TOOL_SCHEMA", "Tool", "ToolKit",
]
