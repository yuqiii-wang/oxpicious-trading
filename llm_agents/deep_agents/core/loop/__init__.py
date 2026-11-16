"""llm_agents.deep_agents.core.loop — the deep-agent loop (the harness).

Deliberately plain — the Claude Code school of ONE loop, no framework:
ONE list of messages, ONE provider call per step, results appended as
``tool`` messages, loop until the model replies without a tool call (or
``max_steps`` runs out). The transcript (system prompt, every assistant
message, every tool call and result) is kept on ``AgentResult.tool_calls``
so callers can show what the agent actually did.

2026-11-15 the former flat ``loop.py`` (1,936 lines) split into this
SUBPACKAGE, same-named, zero caller churn (the planning/ 2026-11-07
pattern): the concern classes compose into ONE ``DeepAgent`` —

  * ``result``    — ``AgentResult``, the result payload;
  * ``base``      — ``DeepAgentBase``: the ctor's state + lifecycle
                    (supervision policy, sandbox/tool-prompt wiring,
                    spawn-chain counters, the fork context sanitizer);
  * ``run``       — ``_RunLoop``: the loop itself, the answer gate +
                    output-validation gate, the handback / final-document
                    reserves, the no-orphan-task cleanup;
  * ``tool_exec`` — ``_ToolExecutor``: one tool call (spawner/collector
                    names, builtin tools, MCP fallback) + the scope gate
                    seam + the failure classifier + the preview seam;
  * ``breaker``   — ``_SandboxBreaker``: the write-run-fix loop breaker
                    (bounded nudge lane + the fresh-context repair spawn);
  * ``router``    — ``_JevRouter``: the System One router (spawn / fork /
                    join lanes, the failure probe, the missing-method-safe
                    engine read);
  * ``spawner``   — ``_SubagentSpawner``: the delegation supervision
                    (still loop methods — the supervision state lives in
                    the loop), sync + background spawns + collect + the
                    programmatic ``run_delegation``.

Builtin tools ride beside the MCP tools via ``builtin_tools``
(``core.tools.BuiltinTool``): schemas are appended to the tool list and
``_execute_call`` dispatches name matches to the handler before the MCP
fallback. A SPAWNED CHILD carries its own kit the same way — the input
hook stamps ``SubagentRequest.builtin_tools`` (the thread runner mounts
each child's spawn-scoped plan tools there), and the child ctor merges
them beside its inherited sandbox tools.

The spawner (``spawn_subagent``, sync and background-spawn forms, plus
``collect_subagents`` and the ``run_delegation`` programmatic entry the
step graph uses for delegated plan steps) is a method-level part of
this loop: delegation records, schemas and hooks live in
``core.delegation``, the policy (timeout, fan-out cap, answer-gating on
uncollected spawns, no-orphan-task cleanup) is enforced in the spawner
mixin. Children carry the spawner themselves below the configured spawn
depth (``limits.run_limits().max_spawn_depth``) — a subagent may spawn
sub-sub agents, and the whole supervision policy (timeout,
concurrency, hooks) propagates one level down with them.

A ``sandbox`` (``core.sandbox``) rides along when passed on the ctor:
its ``sandbox_*`` tools merge into ``builtin_tools`` (caller-supplied
names win), the sandbox usage contract is appended to the system
prompt, and spawned children INHERIT it — one shared scratch directory
per run, parent and subagents alike.

Every request the loop sends (and every spawned child, and the planner
session through the same provider instance) carries the provider's
PER-REQUEST token ceiling (``max_tokens`` — ``core.provider``), the
loose runaway guard that lets the runner's step limits stay generous.
"""
from __future__ import annotations

from llm_agents.deep_agents.core.loop.base import (
    DEFAULT_SYSTEM_PROMPT,
    DeepAgentBase,
)
from llm_agents.deep_agents.core.loop.breaker import _SandboxBreaker
from llm_agents.deep_agents.core.loop.result import AgentResult
from llm_agents.deep_agents.core.loop.router import _JevRouter
from llm_agents.deep_agents.core.loop.run import _RunLoop
from llm_agents.deep_agents.core.loop.spawner import _SubagentSpawner
from llm_agents.deep_agents.core.loop.tool_exec import _ToolExecutor


class DeepAgent(DeepAgentBase, _RunLoop, _ToolExecutor, _SandboxBreaker,
                _JevRouter, _SubagentSpawner):
    """One goal -> tool-calling loop -> one answer."""
