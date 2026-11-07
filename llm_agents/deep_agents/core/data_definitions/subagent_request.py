"""SubagentRequest — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.core.delegation` (one file per ``@dataclass``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Awaitable, Callable
from llm_agents.deep_agents.core.tools import BuiltinTool


SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused research agent with read-only database tools "
    "(MCP). Work step by step: inspect schemas with db_list_tables / "
    "db_describe_table before querying, keep SQL narrow (aggregate / "
    "filter — results are row-capped), and when you have enough "
    "evidence, stop calling tools and answer concisely in the user's "
    "language."
)


SubagentInputHook = Callable[["SubagentRun"], Awaitable[None]]


SubagentOutputHook = Callable[["SubagentRun"], Awaitable[str]]


SubagentRunWrapper = Callable[["SubagentRun"], Optional["AbstractContextManager"]]


@dataclass
class SubagentRequest:
    """What a spawned child is asked to do — the input hook's mutation
    surface. Built from the model's tool arguments (goal / max_steps /
    background) plus the spawner's depth bookkeeping; system, data and
    builtin_tools are parent-side defaults a hook may override (the
    runner's hook mounts the child's fork-scoped plan tools and teaches
    them in the system text). The ``subagent_*_hook`` slots carry the
    child's OWN delegation hooks (also stamped by the parent-side input
    hook, below the depth cap) so the child's spawns register one level
    deeper under the same supervision contract."""

    goal: str
    max_steps: int
    background: bool = False
    # The child's spawn depth (root step conversations spawn depth-1
    # children; a depth-1 subagent spawns depth-2 sub-sub agents). At
    # the configured spawn depth (limits.run_limits()) the child gets
    # no spawn tool.
    depth: int = 1
    system: str = SUBAGENT_SYSTEM_PROMPT
    data: Optional[Dict[str, Any]] = None
    builtin_tools: Optional[Dict[str, BuiltinTool]] = None
    # The child's own supervision hooks for ITS spawns (None = the
    # child registers nothing — plain non-graph usage).
    subagent_input_hook: Optional["SubagentInputHook"] = None
    subagent_output_hook: Optional["SubagentOutputHook"] = None
    subagent_run_wrapper: Optional["SubagentRunWrapper"] = None
