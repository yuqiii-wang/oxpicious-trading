"""llm_agents.deep_agents.core.delegation — the subagent ("Task tool")
contract: schemas, run records and supervision hooks.

This is the deep-agent counterpart of Claude Code's Task tool /
DeepSeek Harness's subagent scheduling: the parent's model PROPOSES a
delegation (``spawn_subagent`` — sync, or ``background: true`` for a
detached fork gathered later via
``collect_subagents``); the spawner
OWNS the supervision policy. Everything the spawner and the thread
runner's hooks need to intercept a delegation lives here:

  * the tool schemas the model sees (``SUBAGENT_TOOL_SCHEMA``,
    ``COLLECT_SUBAGENTS_SCHEMA``) and their names;
  * ``SubagentRequest`` — what a child is asked to do; the input
    hook's mutation surface (goal / system / data / the child's own
    builtin-tool kit);
  * ``SubagentRun`` — one delegation's lifecycle record (status
    queued→running→completed/failed/cancelled/timed_out, answer,
    timings, the real ``asyncio.Task`` so cancellers can stop it);
  * the three interception surfaces — input hook (rewrite/veto via
    ``SubagentSpawnRejected``), output hook (rewrite the tool text),
    run wrapper (bind contextvars INSIDE the child task);
  * ``format_subagent_result`` — the default tool-result text, run id
    first so the model can address the fork (collect / cancel by id).

Delegation failures NEVER raise into the loop — timeout, veto, cancel
and child crash all become tool-result text the parent recovers from
(handled in ``core.loop``'s spawner).

NESTED HIERARCHY: the spawn chain is root step conversation (depth 0)
→ subagents (depth 1) → sub-sub agents (depth 2), capped by
``MAX_SPAWN_DEPTH`` — an agent at the cap simply carries no spawn tool
(the LangChain deepagents school of gating recursion structurally
instead of refusing calls at run time). A spawned child inherits its
parent's supervision policy (timeout, concurrency cap, hooks) so every
level is supervised; the thread-side input hook additionally stamps the
child's OWN hook trio on ``SubagentRequest`` so the child's spawns
register as child states one level deeper in the thread tree.
"""
from __future__ import annotations

import asyncio
import datetime
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import (
    Any, Awaitable, Callable, Dict, Literal, Optional,
)

from llm_agents.deep_agents.core.tools import BuiltinTool

# The spawn-depth cap: root step conversations are depth 0, their
# subagents depth 1, sub-sub agents depth 2 — an agent AT the cap gets
# no spawn tool (recursion is gated structurally, never refused at call
# time). One level of "further investigation" below a subagent, exactly
# the hierarchy the thread graph renders.
MAX_SPAWN_DEPTH = 2

# The focused DB prompt every spawned child works from. Children below
# the depth cap get DELEGATION_GUIDANCE appended (they carry the spawn
# tool); children at the cap keep this floor — no spawn tool, so the
# prompt never mentions one (mentioning it would only invite
# hallucinated calls).
SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused research agent with read-only database tools "
    "(MCP). Work step by step: inspect schemas with db_list_tables / "
    "db_describe_table before querying, keep SQL narrow (aggregate / "
    "filter — results are row-capped), and when you have enough "
    "evidence, stop calling tools and answer concisely in the user's "
    "language."
)

# The spawner guidance shared by every agent that carries the spawn
# tool — the root's DEFAULT_SYSTEM_PROMPT and (below the depth cap)
# every spawned child's system text.
DELEGATION_GUIDANCE = (
    " For self-contained sub-questions, prefer delegating to a subagent "
    "via the spawn_subagent tool instead of exploring inline — the "
    "subagent's intermediate work stays out of this context. When your "
    "work splits into NON-OVERLAPPING sub-questions (different subjects "
    "or different indicators), issue ALL their spawn calls together in "
    "ONE step as sibling calls — sibling delegations run in parallel, "
    "while spawning one at a time serializes the work."
)

SUBAGENT_TOOL_NAME = "spawn_subagent"
COLLECT_TOOL_NAME = "collect_subagents"

SUBAGENT_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": SUBAGENT_TOOL_NAME,
        "description": (
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
        ),
        "parameters": {
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
        },
    },
}

COLLECT_SUBAGENTS_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": COLLECT_TOOL_NAME,
        "description": (
            "Gather the results of background subagents (spawned with "
            "background=true). Reports each requested fork's finished "
            "answer or its still-running status; with wait=true (the "
            "default) it blocks until the requested forks finish (up "
            "to timeout seconds) before reporting. Call it before your "
            "final answer — an uncollected fork blocks the answer. "
            "Call it with no arguments to collect every outstanding "
            "fork."
        ),
        "parameters": {
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
        },
    },
}


SubagentStatus = Literal[
    "queued", "running", "completed", "failed", "cancelled", "timed_out",
]

SubagentInputHook = Callable[["SubagentRun"], Awaitable[None]]
SubagentOutputHook = Callable[["SubagentRun"], Awaitable[str]]
# Runs INSIDE the child task around the child's whole agent loop (sync
# context-manager factory — contextvars set here reach the child's LLM
# calls but not the parent's). None returned = wrap nothing.
SubagentRunWrapper = Callable[["SubagentRun"], Optional["AbstractContextManager"]]


class SubagentSpawnRejected(Exception):
    """Raised by a subagent input hook to veto a delegation."""


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
    # MAX_SPAWN_DEPTH the child gets no spawn tool.
    depth: int = 1
    system: str = SUBAGENT_SYSTEM_PROMPT
    data: Optional[Dict[str, Any]] = None
    builtin_tools: Optional[Dict[str, BuiltinTool]] = None
    # The child's own supervision hooks for ITS spawns (None = the
    # child registers nothing — plain non-graph usage).
    subagent_input_hook: Optional["SubagentInputHook"] = None
    subagent_output_hook: Optional["SubagentOutputHook"] = None
    subagent_run_wrapper: Optional["SubagentRunWrapper"] = None


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
    # True from spawn when the run detached (background fork): its
    # result waits in the agent's background queue until collected.
    background: bool = False
    task: Optional["asyncio.Task[str]"] = field(
        default=None, repr=False, compare=False)


def format_subagent_result(run: SubagentRun) -> str:
    """The default tool-result text for a completed subagent — also the
    raw form an output hook receives via ``format_subagent_result``.
    The run id LEADS so the model can address the fork later (collect /
    cancel by id)."""
    return (f"subagent {run.run_id} finished ({run.steps} step(s), "
            f"{run.tool_call_count} tool call(s)):\n{run.answer}")

