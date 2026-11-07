"""BuiltinTool — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.tools.base` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict


@dataclass
class BuiltinTool:
    """A plain built-in tool beside the MCP tools: its chat-completion
    schema plus the async handler for the model's parsed arguments."""

    schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Awaitable[str]]
