"""llm_agents.deep_agents.core.delegation — the subagent ("Task tool")
contract: run records and supervision hooks.

This is the deep-agent counterpart of Claude Code's Task tool /
DeepSeek Harness's subagent scheduling: the parent's model PROPOSES a
delegation (``spawn_subagent`` — sync, or ``background: true`` for a
detached spawn gathered later via
``collect_subagents``); the spawner
OWNS the supervision policy. Everything the spawner and the thread
runner's hooks need to intercept a delegation lives here:

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
    first so the model can address the spawn (collect / cancel by id).

Delegation failures NEVER raise into the loop — timeout, veto, cancel
and child crash all become tool-result text the parent recovers from
(handled in ``core.loop``'s spawner).

NESTED HIERARCHY: the spawn chain is root step conversation (depth 0)
→ subagents (depth 1) → sub-sub agents (depth 2), capped by the
configured spawn depth (``limits.run_limits().max_spawn_depth``) — an
agent at the cap simply carries no spawn tool (the LangChain
deepagents school of gating recursion structurally instead of refusing
calls at run time). A spawned child inherits its parent's supervision
policy (timeout, concurrency cap, hooks) so every level is supervised;
the thread-side input hook additionally stamps the child's OWN hook
trio on ``SubagentRequest`` so the child's spawns register as child
states one level deeper in the thread tree.
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
from llm_agents.deep_agents.tools.delegation_tools import (
    COLLECT_SUBAGENTS_SCHEMA, FORK_TOOL_NAME, FORK_TOOL_SCHEMA,
    SUBAGENT_TOOL_SCHEMA, SUBAGENT_TOOL_NAME,
)

from llm_agents.deep_agents.core.data_definitions import (
    SubagentRequest,
    SubagentRun,
    SUBAGENT_SYSTEM_PROMPT,
    SubagentInputHook,
    SubagentOutputHook,
    SubagentRunWrapper,
    SubagentStatus,
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
    "while spawning one at a time serializes the work. When the next "
    "work chunk instead CONTINUES this conversation — it needs the "
    "context already established here but would burn your step budget "
    "or derail your main line — fork_subagent is the lane: the forked "
    "node inherits this full conversation and hands back the result."
)

SUBAGENT_TOOL_NAME = "spawn_subagent"
COLLECT_TOOL_NAME = "collect_subagents"
FORK_TOOL_NAME = "fork_subagent"

# The model-facing schemas ride the tool layer now (tools.delegation_
# tools — the Tool objects that also seed the ai_prompts tool rows);
# re-exported under the historical names for the loop and the CLI.




class SubagentSpawnRejected(Exception):
    """Raised by a subagent input hook to veto a delegation."""




def format_subagent_result(run: SubagentRun) -> str:
    """The default tool-result text for a finished subagent — also the
    raw form an output hook receives via ``format_subagent_result``.
    The run id LEADS so the model can address the spawn later (collect /
    cancel by id). STATUS-AWARE (2026-11-15, run d7b24a36): a failed
    child renders its error and its REAL spent usage — the old
    unconditional `finished (N step(s)...)` shape reported a failed
    child as `finished (0 step(s), 0 tool call(s)): None`, and the
    supervisor's reviews diagnosed a 32-step analysis as a zero-work
    spawn hiccup off exactly that text."""
    if run.status in ("failed", "cancelled", "timed_out"):
        return (f"subagent {run.run_id} {run.status} "
                f"({run.steps} step(s), {run.tool_call_count} tool "
                f"call(s) were spent): {run.error or 'no answer'}")
    return (f"subagent {run.run_id} finished ({run.steps} step(s), "
            f"{run.tool_call_count} tool call(s)):\n{run.answer}")
