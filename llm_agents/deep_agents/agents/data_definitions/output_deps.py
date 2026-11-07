"""OutputDeps — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.agents.output_agent` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from llm_agents.deep_agents.core.loop import DeepAgent


@dataclass(frozen=True)
class OutputDeps:
    """One output node's runner-side collaborators (injected — this
    module imports no langgraph; composition stays in the graph
    builder)."""

    # () -> the synthesis conversation's user message (the graph
    # builder's step_user_message composition over the fixed directive
    # + the findings bridge).
    build_message: Callable[[], str]
    # () -> the builtin_tools dict (stats / kg / prompt-fetch kits —
    # NO plan kit: nothing left to rework at an output seat).
    make_tools: Callable[[], Dict[str, Any]]
    # () -> this run's shared sandbox cell.
    sandbox: Callable[[], Optional[Any]]
    # The composed system prompt (standing + base + step addendum).
    system: str
    # The DeepAgent kwargs shared by every conversation of the run
    # (model / max_steps / temperature / tool_prompts).
    common: Dict[str, Any]
    # The MCP tool client (injected — core, not langgraph).
    mcp: Any
    # The conversation engine class (core.loop.DeepAgent; injectable
    # for stub tests).
    agent_factory: Callable[..., Any] = DeepAgent
