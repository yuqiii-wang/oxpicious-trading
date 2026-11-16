"""llm_agents.deep_agents.tools.delegation_tools — the delegation tools
as OBJECTS.

``spawn_subagent`` / ``collect_subagents`` are the delegation contract's
model-facing tools. Their HANDLERS are deliberately method-level parts
of the agent loop (``core.loop`` intercepts the two names before the
builtin fallback: spawning registers a supervised child task in the
loop's own registry, collection reads its background-spawn bookkeeping —
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
FORK_TOOL_NAME = "fork_subagent"


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
                "description": "Spawn the subagent in the "
                               "background: this call returns "
                               "immediately with the run id and "
                               "the subagent runs while you keep "
                               "working (use it for delegated plan "
                               "steps); omit when you need the "
                               "answer right now.",
            },
            "study_focus": {
                "type": "string",
                "description": "EXPLICIT DB-scope extension (rare): "
                               "name the study DOMAIN this child must "
                               "read BEYOND your own authorized tables "
                               "(e.g. '期权持仓持仓量' / 'margin "
                               "trading balance') — the harness "
                               "KG-locates the related tables and "
                               "grants the child exactly those. "
                               "Omit it and the child inherits your "
                               "table scope verbatim; a child cannot "
                               "widen its own scope.",
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
        "prompts), so the goal states THIS spawn's question. Its "
        "final answer comes back as this tool's result (or via "
        "collect_subagents for a background spawn) — read it before "
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


class ForkSubagentTool(Tool):
    """The ``fork_subagent`` tool object — the schema and the seed
    source; the loop's ``DeepAgent._run_fork_subagent`` owns the
    runtime. The spawn/fork contrast is the whole description: a spawn
    child starts FRESH (goal only), a fork child CONTINUES this
    conversation (the full context above is inherited) and works the
    forked objective with everything already established."""

    name: ClassVar[str] = FORK_TOOL_NAME
    db_key: ClassVar[Tuple[str, str]] = (FORK_TOOL_NAME, "delegation")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "The objective the forked continuation "
                               "works — the conversation above already "
                               "carries the background, so this states "
                               "ONLY what remains to do and what to "
                               "return.",
            },
            "max_steps": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional tool-step budget for the "
                               "forked node (default: this agent's "
                               "own budget).",
            },
        },
        "required": ["goal"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Fork this conversation: a subagent node CONTINUES from the "
        "full context above (every message, tool result and script "
        "already in play — unlike spawn_subagent, whose subagent sees "
        "ONLY the goal you write) and works the objective you give, "
        "starting from where this conversation stands. The forked "
        "node's final answer comes back as this tool's result and "
        "this conversation stays on its own thread. Use fork_subagent "
        "when the next work chunk needs everything discussed so far "
        "but would burn this node's own step budget or derail its "
        "main line — the fork inherits the context so nothing is "
        "re-derived; use spawn_subagent when a fresh, self-contained "
        "sub-question is enough. The fork runs in the FOREGROUND — "
        "this call returns only when the forked node hands back — "
        "and the forked node may itself spawn or fork further subagents "
        "below the depth cap. It shares this sandbox session (its "
        "files and scripts are already visible to it)."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        return (f"{self.name} is dispatched by the agent loop itself "
                "(its handler is the loop's supervised fork spawner); "
                "this object carries the schema and the prompt row "
                "only.")


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
                "description": "The spawn run ids to gather (every "
                               "outstanding spawn when omitted).",
            },
            "wait": {
                "type": "boolean",
                "description": "Block until the requested spawns "
                               "finish (default true).",
            },
            "timeout": {
                "type": "number",
                "minimum": 1,
                "description": "Seconds to wait at most (default "
                               "120; running spawns report their "
                               "status after it).",
            },
        },
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Gather the results of background subagents (spawned with "
        "background=true). Reports each requested spawn's finished "
        "answer or its still-running status; with wait=true (the "
        "default) it blocks until the requested spawns finish (up "
        "to timeout seconds) before reporting. Call it before your "
        "final answer — an uncollected spawn blocks the answer. "
        "Call it with no arguments to collect every outstanding "
        "spawn."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        return (f"{self.name} is dispatched by the agent loop itself "
                "(its handler is the loop's spawn collector); this "
                "object carries the schema and the prompt row only.")


# The chat-completion schemas, in the loop's expected shape — re-exported
# by core.delegation under its historical names.
SUBAGENT_TOOL_SCHEMA: Dict[str, Any] = SpawnSubagentTool().schema
COLLECT_SUBAGENTS_SCHEMA: Dict[str, Any] = CollectSubagentsTool().schema
FORK_TOOL_SCHEMA: Dict[str, Any] = ForkSubagentTool().schema
