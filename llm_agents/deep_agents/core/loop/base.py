"""llm_agents.deep_agents.core.loop.base — DeepAgent's STATE and
lifecycle (the split-off base class, 2026-11-15).

Everything the loop owns BEFORE it runs: the ctor's supervision policy,
the sandbox-inheritance and tool-prompt wiring, the spawn-chain state
(breaker streaks, router budgets, failure-evidence counters — reset per
``run()``), the ``_can_spawn`` depth gate, the parent-side subagent
lifecycle controls and the fork child's opening-transcript sanitizer.
The behavior mixins compose on top of this in the package ``__init__``
(``run`` / tool execution / the sandbox breaker / the System One
router / the spawner) — every field here is read there by name, so the
state story stays in ONE place.
"""
from __future__ import annotations

import asyncio
import copy
from typing import (
    TYPE_CHECKING, Any, Dict, List, Optional, Tuple,
)

from llm_agents.deep_agents.core.authorization import (
    AuthorizationContext, SqlScopeGate,
)
from llm_agents.deep_agents.core.delegation import (
    DELEGATION_GUIDANCE,
    SUBAGENT_SYSTEM_PROMPT,
    SubagentInputHook,
    SubagentOutputHook,
    SubagentRun,
    SubagentRunWrapper,
)
from llm_agents.deep_agents.core.mcp import McpToolClient
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.core.tools import BuiltinTool
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.tools.sandbox_tools import SandboxToolSet

if TYPE_CHECKING:
    from llm_agents.deep_agents.core.sandbox import Sandbox

# The root's default prompt: the focused base plus the delegation
# guidance shared by EVERY agent that carries the spawn tool (root step
# conversations and, below the configured spawn depth, spawned
# children — a child at the cap gets the focused base alone, no spawn
# mention).
_BASE_SYSTEM_PROMPT = SUBAGENT_SYSTEM_PROMPT
DEFAULT_SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT + DELEGATION_GUIDANCE


class DeepAgentBase:
    """The loop's state + lifecycle; composed into ``DeepAgent`` (the
    package ``__init__``). One goal -> tool-calling loop -> one answer."""

    def __init__(
        self, provider: ZhipuChatProvider, mcp: McpToolClient, *,
        model: Optional[str] = None,
        max_steps: int = 12,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        allow_subagents: bool = True,
        spawn_depth: int = 0,
        builtin_tools: Optional[Dict[str, BuiltinTool]] = None,
        sandbox: Optional["Sandbox"] = None,
        tool_prompts: Optional[Dict[str, str]] = None,
        subagent_timeout: Optional[float] = None,
        max_concurrent_subagents: int = 4,
        subagent_input_hook: Optional[SubagentInputHook] = None,
        subagent_output_hook: Optional[SubagentOutputHook] = None,
        subagent_run_wrapper: Optional[SubagentRunWrapper] = None,
        decision_engine: Optional[Any] = None,
        node_role: str = "",
        fork_child: bool = False,
        authorization: Optional[AuthorizationContext] = None,
    ) -> None:
        self._provider = provider
        self._mcp = mcp
        self._model = model
        self._max_steps = max(1, max_steps)
        self._system = system if system is not None else DEFAULT_SYSTEM_PROMPT
        self._temperature = temperature
        self._allow_subagents = allow_subagents
        # This agent's rung on the spawn chain (0 = a root step
        # conversation). The spawn/collect tools ride only below the
        # configured spawn depth — the cap gates recursion structurally.
        self._spawn_depth = max(0, int(spawn_depth))
        self._sandbox = sandbox
        # The run-start resolution of the tool-description rows
        # (llm.ai_prompts prompt_type 4 — tools.base's override
        # contract): applied to the sandbox tools mounted HERE and
        # inherited by spawned children (caller-supplied builtin_tools
        # apply their own at kit construction).
        self._tool_prompts = tool_prompts or None
        self._builtin_tools = dict(builtin_tools) if builtin_tools else {}
        if sandbox is not None:
            # The sandbox_* tools fill the unset slots via the tool
            # layer's kit (DB-description overrides applied); a
            # caller-supplied builtin keeps its name.
            kit = SandboxToolSet(sandbox, descriptions=self._tool_prompts)
            for name, tool in kit.builtin_tools().items():
                self._builtin_tools.setdefault(name, tool)
            # The tools exist → the prompt must teach them; appended to
            # the caller's system text too (the runner renders its
            # templates into that text before this ctor runs).
            self._system = f"{self._system}\n\n{kit.prompt_addendum()}"
        # Parent-side supervision policy for delegations (never
        # model-controlled): wall-clock bound per child, how many may
        # run at once, and the input/output interception hooks. The
        # whole policy propagates to spawned children — every level of
        # the hierarchy is supervised by its parent.
        self._subagent_timeout = (
            float(subagent_timeout) if subagent_timeout else None)
        self._max_concurrent_subagents = max(1, max_concurrent_subagents)
        self._subagent_slots = asyncio.Semaphore(self._max_concurrent_subagents)
        self._subagent_input_hook = subagent_input_hook
        self._subagent_output_hook = subagent_output_hook
        self._subagent_run_wrapper = subagent_run_wrapper
        # The System One decision engine (decision/, None = the
        # legacy lanes judge) — the spawn gate is its only reader.
        self._decision_engine = decision_engine
        # This node's ROLE for the System One router (SubagentRequest
        # .role — '' a biz/inference conversation, 'scripting' a node
        # spawned to write/run scripts, 'investigator' a failure
        # probe): a scripting node is never router-spawned again and is
        # the join lane's watch target.
        self._node_role = str(node_role or "")
        # THE FORK CHILD flag (2026-11-13): set on inherit-context
        # children (``SubagentRequest.inherit_transcript``). A fork
        # shares the handback reserve's contract — the parent awaits it
        # foreground, so one step before the cap it hands its evidence
        # back instead of dying at the cap (the scripting-role gate
        # extends with this flag).
        self._fork_child = bool(fork_child)
        # THE SCOPE AUTHORIZATION (core/authorization, 2026-11-13): this
        # conversation's resolved DB-table scope — the run's thread
        # temp-user context, or a spawned node's extension context. None
        # = ungated (plain non-graph usage / the kill-switch callers);
        # when set, every gated MCP call (db_run_query and friends) is
        # checked BEFORE the shared daemon sees it, and spawned children
        # inherit it verbatim unless their request carries an extension.
        self._authorization = authorization
        self._scope_gate = SqlScopeGate()
        self._subagent_runs: List[SubagentRun] = []
        # Background-spawn bookkeeping: finished results by run id (post
        # output-hook) and the ids the model has gathered — the final
        # answer gate reads them (an uncollected spawn blocks finishing).
        self._background_results: Dict[str, str] = {}
        self._background_collected: set = set()
        # Output-validation repair rounds spent (see
        # limits.max_output_repairs; reset per run()).
        self._output_repairs = 0
        # The sandbox write-run-fix loop breaker (see limits.sandbox_
        # failure_*; reset per run()): consecutive failed sandbox-tool
        # outcomes, and the nudges already spent against the
        # per-conversation cap.
        self._sandbox_fail_streak = 0
        self._sandbox_nudges_used = 0
        # The breaker's fresh-context escalation (see limits.sandbox_
        # spawn_*): consecutive TRACEBACK-class script failures, the
        # repair spawns already spent, the failed runs' exception lines
        # (the spawn handoff's fix history) and the failing script path.
        self._sandbox_syntax_streak = 0
        self._sandbox_spawns_used = 0
        self._sandbox_fail_lines: List[str] = []
        self._sandbox_script_path = ""
        # The System One ROUTER state (2026-11-12, see _route_response):
        # router spawns spent, the join round (nudged once, then any
        # further tool-call reply forces the join handback), and the
        # failure-probe investigator spawns spent on failed children.
        self._route_spawns_used = 0
        self._route_forks_used = 0
        self._pending_fork_cut: Optional[int] = None
        self._pending_fork_note: Optional[str] = None
        self._join_nudged = False
        self._route_probes_used = 0
        # The OUTPUT SEAT's reserve once-guard (2026-11-15): distinct
        # from _join_nudged — the output node has no join lane; its
        # reserve only appends the final-document nudge.
        self._output_reserve_nudged = False
        # THE FAILURE-EVIDENCE counters (2026-11-15, run d7b24a36): the
        # usage a FAILED run reports through its SubagentRun — a child
        # dying at the step cap (or any crash) must not read as
        # "0 step(s), 0 tool call(s)" to its parent and the supervisor
        # (the live run's reviews diagnosed a 32-step analysis as a
        # zero-work spawn hiccup off exactly that artifact).
        self._steps_used = 0
        self._tool_calls_used = 0

    @property
    def _can_spawn(self) -> bool:
        """Whether this agent carries the spawn/collect tools: the
        master switch on AND below the depth cap."""
        return (self._allow_subagents
                and self._spawn_depth < run_limits().max_spawn_depth)

    # ------------------------------------------------------------------
    # Parent-side subagent lifecycle control
    # ------------------------------------------------------------------

    def subagent_runs(self) -> Tuple[SubagentRun, ...]:
        """Snapshot of every delegation this agent (or its children's
        spawner) has recorded, in spawn order."""
        return tuple(self._subagent_runs)

    def cancel_subagent(self, run_id: str) -> bool:
        """Cancel one running/queued subagent; True when a live task was
        cancelled. The awaiting step sees the cancellation as the tool
        result ``spawn_subagent: cancelled by parent`` and carries on."""
        for run in self._subagent_runs:
            if run.run_id == run_id and run.task is not None \
                    and not run.task.done():
                run.task.cancel()
                return True
        return False

    # ------------------------------------------------------------------
    # The fork: the inherit-context child's opening transcript
    # ------------------------------------------------------------------

    def _fork_inherited_context(
            self, inherited: List[Dict[str, Any]],
            directive_message: str) -> List[Dict[str, Any]]:
        """The fork child's opening context (2026-11-13): the child's
        OWN system + a DEEP COPY of the parent transcript + the fork
        directive. The copy is sanitized — the parent's leading system
        message is dropped (the child wears its own role prompt) and a
        trailing assistant-with-tool_calls round is dropped (its tool
        results are not in the copy — the fork was intercepted before
        they ran; the protocol requires results after calls). The
        ``fork_inherit_max_messages`` cap keeps the first user message
        (the run goal) plus the most recent turns with an elision
        marker between, so the inherited context stays a bounded
        budget; 0 disables the cap. The parent's own list is never
        touched."""
        body: List[Dict[str, Any]] = [
            copy.deepcopy(message) for message in inherited
            if str(message.get("role")) != "system"]
        while (body and str(body[-1].get("role")) == "assistant"
               and body[-1].get("tool_calls")):
            body.pop()
        cap = run_limits().fork_inherit_max_messages
        if cap > 0 and len(body) > cap:
            head: List[Dict[str, Any]] = []
            if body and str(body[0].get("role")) == "user":
                head = [body[0]]
                body = body[1:]
            keep = max(1, cap - len(head) - 1)
            if len(body) > keep:
                elided = len(body) - keep
                body = (head + [{
                    "role": "user",
                    "content": (f"[… {elided} earlier turn(s) of the "
                                "inherited conversation elided by the "
                                "fork inherit cap — the most recent "
                                "ones follow …]"),
                }] + body[-keep:])
            else:
                body = head + body
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system}]
        messages.extend(body)
        messages.append({"role": "user", "content": directive_message})
        return messages
