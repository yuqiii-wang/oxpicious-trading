"""SubagentRequest / SubagentRun — data definitions.

Migrated verbatim from :mod:`llm_agents.deep_agents.core.delegation` (one file per ``@dataclass``).
2026-11-07: the pair is ONE MODULE again — pydantic resolves every
dataclass field annotation against its OWN module's globals, and these
two are mutually referential (``SubagentRun.request`` →
``SubagentRequest``; the hook ALIASES below carry ``"SubagentRun"``
forward refs), so each file needs the other's name importable — the
two-file split deferred every ``AgentResult`` schema build
(``subagent_runs: List[SubagentRun]``) and no agent could answer.
``subagent_run.py`` stays as the re-export shim for the import path."""

from __future__ import annotations

import asyncio
import datetime
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import (
    Any, Awaitable, Callable, Dict, List, Literal, Optional,
)
from llm_agents.deep_agents.core.tools import BuiltinTool


SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused research agent with read-only database tools "
    "(MCP). Work step by step: inspect schemas with db_list_tables / "
    "db_describe_table before querying, keep each SQL statement small "
    "— one narrow retrieval (the needed columns, filtered on the "
    "table's key grain), then aggregate in pandas or a follow-up "
    "small query; a multi-CTE monolith fails as a whole and results "
    "are row-capped — and when you have enough evidence, stop calling "
    "tools and answer concisely in the user's language."
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
    runner's hook mounts the child's spawn-scoped plan tools and teaches
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
    # An extra TEXT block appended to the child's first user message
    # (after the structured-context fence) — the parent-side hook's
    # per-child addition, e.g. the MATERIALIZED-DATA hints block the
    # spawn's goal KG-searched (langgraph.data_hints). None = nothing
    # appended.
    user_addendum: Optional[str] = None
    builtin_tools: Optional[Dict[str, BuiltinTool]] = None
    # The child's NODE ROLE for the System One router (core/loop.py,
    # 2026-11-12): '' = a biz/inference conversation (the router may
    # spawn its proposed script work away), 'scripting' = a node spawned
    # to write/run scripts (the router never spawns it again — its
    # coding is ITS work; the join lane watches its messiness),
    # 'investigator' = a failure probe (reads the failed node's
    # recorded traffic). Stamped by the HARNESS spawns (the router's
    # scripting handoff, the repair spawn, the failure probe); the
    # model-called spawn tool carries no role (the default '').
    role: str = ""
    # THE FORK INHERITANCE (2026-11-13): the parent's TRANSCRIPT the
    # child continues from — None (the default) keeps the spawn
    # semantics (fresh 2-message context, never the parent transcript).
    # When set, the child's conversation opens with its OWN system +
    # this copy (deep-copied by the spawner, leading system message and
    # any trailing unanswered tool-call round dropped) + the fork
    # directive user message — an inherit-context child, the deliberate
    # exception to the fresh-context rule. Foreground only: a detached
    # fork would diverge from the parent's living copy.
    inherit_transcript: Optional[List[Dict[str, Any]]] = None
    # THE DRIFT FORK's abandoned tail (2026-11-13): when the fork cut
    # an earlier checkpoint out of the transcript, everything AFTER the
    # cut was left behind — this note (the drifted segment's head +
    # turn count) rides to the input hook, which materializes the
    # abandoned tail as a kind-2 'abandoned' stub row under the same
    # parent (the UI demotes it to the right-side edge path). None =
    # fork-from-now (nothing abandoned).
    fork_abandoned_note: Optional[str] = None
    # The child's own supervision hooks for ITS spawns (None = the
    # child registers nothing — plain non-graph usage).
    subagent_input_hook: Optional["SubagentInputHook"] = None
    subagent_output_hook: Optional["SubagentOutputHook"] = None
    subagent_run_wrapper: Optional["SubagentRunWrapper"] = None


SubagentStatus = Literal[
    "queued", "running", "completed", "failed", "cancelled", "timed_out",
]


@dataclass
class SubagentRun:
    """One delegation's lifecycle record — registry entry and the hooks'
    interception surface."""

    run_id: str
    request: SubagentRequest
    status: SubagentStatus = "queued"
    answer: Optional[str] = None
    error: Optional[str] = None
    steps: int = 0
    tool_call_count: int = 0
    started_at: Optional[datetime.datetime] = None
    ended_at: Optional[datetime.datetime] = None
    # True from spawn when the run detached (background spawn): its
    # result waits in the agent's background queue until collected.
    background: bool = False
    # The registry stamps the child's plan-tree ids back onto the run
    # (the spawn hooks after create_child_state — fail-soft None when
    # no store / the row failed): the FAILURE-PROBE lane reads them to
    # hand the investigator the failed node's exact references.
    state_id: Optional[int] = None
    llm_context_id: Optional[str] = None
    task: Optional["asyncio.Task[str]"] = field(
        default=None, repr=False, compare=False)
