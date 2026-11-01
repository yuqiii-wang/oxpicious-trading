"""llm_agents.deep_agents.core — the agent RUNTIME: the loop and the
capabilities that plug into it (the harness layer).

The layering follows the modern harness split (DeepSeek Harness's
"everything is a plugin" around the loop; Claude Code's loop + tools +
subagents): ONE plain agent loop, with every capability a replaceable
module beside it —

  * ``provider`` — the model transport (ZhiPu chat completions with
    tool declarations);
  * ``mcp``       — the tool transport (an MCP session exposed as
    chat-completion tool schemas; the repo's read-only DB daemon by
    default);
  * ``tools``     — the builtin-tool plumbing (BuiltinTool) and the
    user-message assembly;
  * ``delegation``— the subagent contract (schemas, SubagentRun/
    SubagentRequest, the supervision hooks) — the Task-tool layer;
  * ``loop``      — DeepAgent: the loop itself, the spawner (sync +
    background forks + collect + the programmatic run_delegation), and
    the answer-gating / no-orphan-task policies;
  * ``sandbox``   — the code-execution capability (one scratch
    directory per run, sandbox_* builtin tools).
"""
from llm_agents.deep_agents.core.delegation import (
    COLLECT_SUBAGENTS_SCHEMA,
    COLLECT_TOOL_NAME,
    SUBAGENT_TOOL_NAME,
    SUBAGENT_TOOL_SCHEMA,
    SubagentRequest,
    SubagentRun,
    SubagentSpawnRejected,
    format_subagent_result,
)
from llm_agents.deep_agents.core.loop import (
    DEFAULT_SYSTEM_PROMPT, AgentResult, DeepAgent,
)
from llm_agents.deep_agents.core.mcp import McpToolClient
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.core.sandbox import Sandbox
from llm_agents.deep_agents.core.tools import BuiltinTool

__all__ = [
    "AgentResult", "BuiltinTool", "COLLECT_SUBAGENTS_SCHEMA",
    "COLLECT_TOOL_NAME", "DEFAULT_SYSTEM_PROMPT", "DeepAgent",
    "McpToolClient", "SUBAGENT_TOOL_NAME", "SUBAGENT_TOOL_SCHEMA",
    "Sandbox", "SubagentRequest", "SubagentRun", "SubagentSpawnRejected",
    "ZhipuChatProvider", "format_subagent_result",
]
