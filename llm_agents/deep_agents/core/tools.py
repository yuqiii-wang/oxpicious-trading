"""llm_agents.deep_agents.core.tools — the builtin-tool plumbing and the
user-message assembly every conversation shares.

A BUILTIN TOOL is a plain function of the model's arguments riding
beside the MCP tools: its chat-completion schema plus an async handler.
The loop appends the schemas to the tool list and dispatches name
matches to the handler before the MCP fallback; handler failures come
back as the tool's result text (the loop must survive a broken tool).
No registry, no lifecycle — everything stateful (the spawner, the
sandbox) BUILDS a BuiltinTool and hands it to the agent's ctor, which
is how the thread runner mounts its plan kits and the sandbox mounts
its ``sandbox_*`` tools.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional


@dataclass
class BuiltinTool:
    """A plain built-in tool beside the MCP tools: its chat-completion
    schema plus the async handler for the model's parsed arguments.
    Handler failures come back as the tool's result text (the loop
    must survive a broken tool)."""

    schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Awaitable[str]]


def user_content(goal: str, data: Optional[Dict[str, Any]] = None) -> str:
    """The user message: the goal, plus *data* (whatever JSON object the
    caller wants the run to see — analysis items, skills, …) appended as
    a fenced structured-context block."""
    if not data:
        return goal
    return (f"{goal}\n\n[Structured context — JSON]\n"
            f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```")


