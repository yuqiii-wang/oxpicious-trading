"""llm_agents.deep_agents.tools.delegation_tools — the delegation tools
as OBJECTS.

``spawn_subagent`` / ``collect_subagents`` are the delegation contract's
model-facing tools. Their HANDLERS are deliberately method-level parts
of the agent loop (``core.loop`` intercepts the two names before the
builtin fallback: spawning registers a supervised child task in the
loop's own registry, collection reads its background-fork bookkeeping —
neither can live outside the loop without duplicating its supervision
state). What belongs in the tool LAYER — the schemas the model sees,
and the ``llm.ai_prompts`` tool rows' seed source — lives here as the
two :class:`Tool` objects.

``core.delegation`` re-exports the schema constants (``SUBAGENT_TOOL_
SCHEMA`` / ``COLLECT_SUBAGENTS_SCHEMA``) and names from this module for
its existing importers; the loop dispatches by name, so these tools'
:meth:`run` is intentionally unreachable (it refuses with a pointer).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Tuple

from llm_agents.deep_agents.tools.base import Tool

SUBAGENT_TOOL_NAME = "spawn_subagent"
COLLECT_TOOL_NAME = "collect_subagents"


class SpawnSubagentTool(Tool):
    """The ``spawn_subagent`` tool object — the schema and the seed
    source; the loop's spawner (``DeepAgent._run_subagent``) owns the
    runtime."""

    name: ClassVar[str] = SUBAGENT_TOOL_NAME
    db_key: ClassVar[Tuple[str, str]] = (SUBAGENT_TOOL_NAME, "delegation")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The complete, self-contained "
                               "instruction for the subagent: open "
                               "with the business question (the "
                               "subject + what to settle), then "
                               "the evidence to weigh and the "
                               "tables/columns to use.",
            },
            "max_steps": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional tool-step budget for the "
                               "subagent (default: this agent's own "
                               "budget).",
            },
            "background": {
                "type": "boolean",
                "description": "Fork the subagent in the "
                               "background: this call returns "
                               "immediately with the run id and "
                               "the subagent runs while you keep "
                               "working (use it for delegated plan "
                               "steps); omit when you need the "
                               "answer right now.",
            },
        },
        "required": ["goal"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Spawn a subagent that pursues ONE self-contained business "
        "sub-question with the same MCP tools (read-only DB queries "
        "plus the web_search / web_summarize live web tools) and "
        "returns its final answer. The subagent does not see this "
        "conversation — it receives the goal you write, phrased as "
        "the business question it settles (the subject + the "
        "metric/aspect to study), with every schema/table/column "
        "hint it needs; when the host attaches a run context, the "
        "subagent also sees that (subject, focus question, selected "
        "prompts), so the goal states THIS fork's question. Its "
        "final answer comes back as this tool's result (or via "
        "collect_subagents for a background fork) — read it before "
        "concluding. A subagent may itself spawn further subagents "
        "when its investigation needs it, up to the spawn-depth cap "
        "(the tool is absent at the cap). The spawner supervises "
        "every subagent (concurrency cap and timeout); spawn "
        "failures and timeouts come back as this tool's result. "
        "When your work splits into NON-OVERLAPPING sub-questions, "
        "issue several spawn_subagent calls in ONE step — sibling "
        "calls run in parallel, while one-at-a-time spawns "
        "serialize the work. With background=true the call returns "
        "immediately with the run id and the subagent runs in "
        "parallel while you keep working — gather its result later "
        "with collect_subagents, and note its run id so a plan "
        "update can stop it if the plan changes."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        return (f"{self.name} is dispatched by the agent loop itself "
                "(its handler is the loop's supervised spawner); this "
                "object carries the schema and the prompt row only.")


class CollectSubagentsTool(Tool):
    """The ``collect_subagents`` tool object — schema + seed source;
    the loop's ``DeepAgent._collect_subagents`` owns the runtime."""

    name: ClassVar[str] = COLLECT_TOOL_NAME
    db_key: ClassVar[Tuple[str, str]] = (COLLECT_TOOL_NAME, "delegation")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "run_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The fork run ids to gather (every "
                               "outstanding fork when omitted).",
            },
            "wait": {
                "type": "boolean",
                "description": "Block until the requested forks "
                               "finish (default true).",
            },
            "timeout": {
                "type": "number",
                "minimum": 1,
                "description": "Seconds to wait at most (default "
                               "120; running forks report their "
                               "status after it).",
            },
        },
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Gather the results of background subagents (spawned with "
        "background=true). Reports each requested fork's finished "
        "answer or its still-running status; with wait=true (the "
        "default) it blocks until the requested forks finish (up "
        "to timeout seconds) before reporting. Call it before your "
        "final answer — an uncollected fork blocks the answer. "
        "Call it with no arguments to collect every outstanding "
        "fork."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        return (f"{self.name} is dispatched by the agent loop itself "
                "(its handler is the loop's fork collector); this "
                "object carries the schema and the prompt row only.")


# The chat-completion schemas, in the loop's expected shape — re-exported
# by core.delegation under its historical names.
SUBAGENT_TOOL_SCHEMA: Dict[str, Any] = SpawnSubagentTool().schema
COLLECT_SUBAGENTS_SCHEMA: Dict[str, Any] = CollectSubagentsTool().schema
