"""llm_agents.deep_agents.core.loop — the deep-agent loop (the harness).

Deliberately plain — the Claude Code school of ONE loop, no framework:
ONE list of messages, ONE provider call per step, results appended as
``tool`` messages, loop until the model replies without a tool call (or
``max_steps`` runs out). The transcript (system prompt, every assistant
message, every tool call and result) is kept on ``AgentResult.tool_calls``
so callers can show what the agent actually did.

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
uncollected spawns, no-orphan-task cleanup) is enforced HERE. Children
carry the spawner themselves below the configured spawn depth
(``limits.run_limits().max_spawn_depth``) — a subagent may spawn
sub-sub agents, and the whole supervision policy (timeout,
concurrency, hooks) propagates one level down with them.

A ``sandbox`` (``core.sandbox``) rides along when passed on the ctor:
its ``sandbox_*`` tools merge into ``builtin_tools`` (caller-supplied
names win), the sandbox usage contract is appended to the system
prompt, and spawned children INHERIT it — one shared scratch directory
per run, parent and subagents alike.

Two gates stand before a reply becomes the final answer, both bounded
and both shipping the answer when their rounds run out (the
always-answer contract): the ANSWER GATE (uncollected background spawns
are nudged for collection first — below) and the OUTPUT-VALIDATION
gate (``core.output_validation``): structurally broken replies —
empty, truncated mid-fence, ending on the truncation marker or a
dangling heading, degenerately repetitive, or self-declared JSON that
does not parse — get ONE repair round with the specific complaints fed
back as a user message; the planner session runs the same
propose → validate → correct shape for the plan JSON
(``planning.plan_json``). Every request the loop sends (and every
spawned child, and the planner session through the same provider
instance) carries the provider's PER-REQUEST token ceiling
(``max_tokens`` — ``core.provider``), the loose runaway guard that
lets the runner's step limits stay generous.
"""
from __future__ import annotations

import asyncio
import copy
import datetime
import json
import logging
import uuid
from typing import (
    TYPE_CHECKING, Any, Dict, List, Optional, Tuple,
)

from pydantic import BaseModel, ConfigDict, Field

from llm_agents._core.models import SHANGHAI_TZ
from llm_agents.deep_agents.core.delegation import (
    COLLECT_SUBAGENTS_SCHEMA,
    COLLECT_TOOL_NAME,
    DELEGATION_GUIDANCE,
    FORK_TOOL_NAME,
    FORK_TOOL_SCHEMA,
    SUBAGENT_SYSTEM_PROMPT,
    SUBAGENT_TOOL_NAME,
    SUBAGENT_TOOL_SCHEMA,
    SubagentRequest,
    SubagentRun,
    SubagentSpawnRejected,
    format_subagent_result,
)
from llm_agents.deep_agents.core.mcp import McpToolClient
from llm_agents.deep_agents.core.output_validation import (
    repair_user_message,
    validate_output,
)
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.core.tools import user_content
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.payload_guard import guard_text
from llm_agents.deep_agents.tools.sandbox_tools import SandboxToolSet

if TYPE_CHECKING:
    from llm_agents.deep_agents.core.sandbox import Sandbox

logger = logging.getLogger(__name__)

# The root's default prompt: the focused base plus the delegation
# guidance shared by EVERY agent that carries the spawn tool (root step
# conversations and, below the configured spawn depth, spawned
# children — a child at the cap gets the focused base alone, no spawn
# mention).
_BASE_SYSTEM_PROMPT = SUBAGENT_SYSTEM_PROMPT
DEFAULT_SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT + DELEGATION_GUIDANCE

# The fork child's directive — the user message that closes the
# inherited transcript (2026-11-13): the child CONTINUES the copied
# conversation, so the text states the contract (inherit, do not redo,
# hand back) above the fork's own goal.
_FORK_TAKEOVER_DIRECTIVE = (
    "[Fork takeover] You are a FORK of the conversation above — its "
    "full context is inherited here (same goal, same shared sandbox, "
    "every message and tool result already established). Work ONLY the "
    "objective that follows, continuing from where the conversation "
    "stands; do NOT redo work already done. Your final answer is the "
    "handback the parent reads.")


class AgentResult(BaseModel):
    """The final answer plus how the agent got there (the core loop's
    result payload crossing into the graph layer). ``subagent_runs``
    holds the loop's runtime records (``core.delegation.SubagentRun`` —
    tasks and hooks, not serializable), hence the arbitrary-type
    allowance; everything else is plain."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    answer: str
    steps: int
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    subagent_runs: List[SubagentRun] = Field(default_factory=list)



class DeepAgent:
    """One goal -> tool-calling loop -> one answer."""

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

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    async def run(self, goal: str,
                  data: Optional[Dict[str, Any]] = None,
                  user_addendum: Optional[str] = None,
                  inherited_messages: Optional[List[Dict[str, Any]]] = None,
                  ) -> AgentResult:
        """Pursue *goal* until the model answers without a tool call.

        *data* — an arbitrary JSON object attached as the run's structured
        context (see ``user_content``); the agent reads it like any other
        user-message content. *user_addendum* — an extra TEXT block
        appended after that content (the spawner's per-run addition, e.g.
        the spawn hooks' materialized-data hints block); None = nothing
        appended. *inherited_messages* — the FORK lane (2026-11-13): the
        parent's transcript this conversation continues from; None (the
        default) keeps the spawn semantics — a fresh two-message context.
        """
        user_message = user_content(goal, data)
        if user_addendum:
            user_message = f"{user_message}\n\n{user_addendum}"
        if inherited_messages:
            messages = self._fork_inherited_context(
                inherited_messages, user_message)
        else:
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": self._system},
                {"role": "user", "content": user_message},
            ]
        tools = await self._mcp.tool_schemas()
        tools = tools + [bt.schema for bt in self._builtin_tools.values()]
        if self._can_spawn:
            tools = tools + [SUBAGENT_TOOL_SCHEMA, FORK_TOOL_SCHEMA,
                             COLLECT_SUBAGENTS_SCHEMA]
        tool_calls_log: List[Dict[str, Any]] = []
        runs_before = len(self._subagent_runs)
        self._output_repairs = 0
        self._sandbox_fail_streak = 0
        self._sandbox_nudges_used = 0
        self._sandbox_syntax_streak = 0
        self._sandbox_spawns_used = 0
        self._sandbox_fail_lines = []
        self._sandbox_script_path = ""
        self._route_spawns_used = 0
        self._route_forks_used = 0
        self._pending_fork_cut: Optional[int] = None
        self._pending_fork_note: Optional[str] = None
        self._join_nudged = False
        self._route_probes_used = 0
        # THE REPEAT GUARD's state (2026-11-13, run 4597f052): the
        # previous executed call's (tool, raw-arguments) signature and
        # whether its result was a failure — an IDENTICAL call right
        # after a failure can only repeat it (2893's edit x2, 2905's
        # run x2 with nothing changed in between).
        self._prev_call_sig: Optional[tuple] = None
        self._prev_call_failed = False

        try:
            for step in range(1, self._max_steps + 1):
                message = await self._provider.chat_complete(
                    messages, model=self._model, tools=tools,
                    temperature=self._temperature)
                calls = message.get("tool_calls") or []
                if not calls:
                    outstanding = self._uncollected_spawns()
                    if outstanding:
                        # The answer gate: a spawn the model created but
                        # never gathered must not silently vanish — nudge
                        # once per step; on the last step the stragglers
                        # are stopped and the answer goes out anyway.
                        if step < self._max_steps:
                            ids = ", ".join(r.run_id for r in outstanding)
                            messages.append({
                                "role": "user",
                                "content": (
                                    f"background subagent(s) {ids} still "
                                    "running or uncollected — gather their "
                                    "results with collect_subagents before "
                                    "answering (they are part of the plan)."
                                ),
                            })
                            continue
                        for run in outstanding:
                            if run.task is not None and not run.task.done():
                                run.task.cancel()
                    answer = str(message.get("content") or "").strip()
                    # The output-validation gate: structural defects
                    # (empty, truncated mid-fence, degenerate repetition,
                    # broken JSON...) get ONE bounded repair round — the
                    # complaints go back like the answer gate's nudge; a
                    # still-invalid answer after the round (or on the
                    # last step) ships as-is, logged.
                    issues = validate_output(answer)
                    if issues and self._output_repairs < \
                            run_limits().max_output_repairs \
                            and step < self._max_steps:
                        self._output_repairs += 1
                        logger.info("step %d/%d: output validation "
                                    "repaired (%d issue(s))", step,
                                    self._max_steps, len(issues))
                        messages.append({"role": "assistant",
                                         "content": answer})
                        messages.append({
                            "role": "user",
                            "content": repair_user_message(issues),
                        })
                        continue
                    if issues:
                        logger.warning(
                            "final answer failed output validation "
                            "(shipped anyway): %s", "; ".join(issues))
                    logger.info("step %d/%d: final answer", step,
                                self._max_steps)
                    return AgentResult(
                        answer=answer, steps=step,
                        tool_calls=tool_calls_log,
                        subagent_runs=self._subagent_runs[runs_before:])

                # THE SYSTEM ONE ROUTER (2026-11-12, _route_response):
                # after every LLM response the injected jev engine
                # routes the NEXT work — inference (continue) / spawn (a
                # fresh scripting sub node takes over the proposed
                # sandbox script work, keeping this conversation on its
                # biz inference chain) / fork (2026-11-13: a continuation
                # sub node that INHERITS this transcript continues the
                # work while the parent awaits) / join (a messy scripting
                # node stops and hands back to the parent chain).
                # Mechanical
                # pre-filters first, jev judges only what passes; every
                # read is affirm-only (a below-floor / unavailable read
                # continues the model's own plan). The assistant message
                # lands BEFORE any routed tool results so the transcript
                # stays protocol-shaped on every branch.
                messages.append({
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": calls,
                })
                route = (None if not calls else
                         await self._route_response(goal, message,
                                                    calls, step,
                                                    messages=messages))
                if route == "spawn":
                    handed = await self._spawn_scripting(goal, message,
                                                        calls)
                    if handed is not None:
                        for call in calls:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call.get("id") or "",
                                "content": (
                                    "[System One router] this coding "
                                    "work was routed to a fresh "
                                    "scripting sub node — its result "
                                    "follows"),
                            })
                        messages.append({"role": "user", "content": handed})
                        continue
                    # The delegation path refused the spawn — the model's
                    # own calls execute normally (the work must still
                    # happen); the assistant message above stands.
                elif route == "fork":
                    handed = await self._fork_scripting(goal, message,
                                                        calls,
                                                        transcript=messages,
                                                        cut=self._pending_fork_cut,
                                                        drift_note=self._pending_fork_note)
                    self._pending_fork_cut = None
                    self._pending_fork_note = None
                    if handed is not None:
                        for call in calls:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call.get("id") or "",
                                "content": (
                                    "[System One router] this coding "
                                    "work was forked to a sub node "
                                    "that inherited this conversation's "
                                    "context — its result follows"),
                            })
                        messages.append({"role": "user", "content": handed})
                        continue
                    # The delegation path refused the fork — same
                    # fallback as the spawn lane: the model's own calls
                    # execute normally.
                elif route == "join":
                    self._join_nudged = True
                    for call in calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.get("id") or "",
                            "content": (
                                "[System One join] not executed — this "
                                "node is joining its parent chain now"),
                        })
                    messages.append({
                        "role": "user",
                        "content": (
                            "[System One join] This node's scripting is "
                            "judged messy. STOP calling tools NOW and "
                            "write your HANDBACK in one message: what "
                            "you established, what is still broken "
                            "(the exact error), the script path in the "
                            "shared sandbox, and what the parent should "
                            "do next. Your handback joins the parent "
                            "chain as this node's result."),
                    })
                    continue
                if self._join_nudged and calls:
                    # The authoritative join: the model kept calling
                    # tools after the nudge — the node stops here and
                    # its last text joins the parent chain.
                    answer = (str(message.get("content") or "").strip()
                              or "(no text before the join)")
                    logger.info("step %d/%d: System One join forced",
                                step, self._max_steps)
                    return AgentResult(
                        answer=("[Joined the parent chain — the System "
                                "One router stopped this node] " + answer),
                        steps=step, tool_calls=tool_calls_log,
                        subagent_runs=self._subagent_runs[runs_before:])
                # Sibling calls of one step run concurrently; gather keeps
                # the results in call order and collects failures per call
                # so one bad tool never kills the step. reply_cut: this
                # response ended on the output ceiling — a call whose
                # arguments JSON is cut gets the cause + resend remedy.
                outcomes = await asyncio.gather(
                    *(self._execute_call(call, step,
                                         reply_cut=(message.get(
                                             "finish_reason") == "length"),
                                         transcript=messages)
                      for call in calls),
                    return_exceptions=True,
                )
                for call, outcome in zip(calls, outcomes):
                    name = str((call.get("function") or {}).get("name") or "")
                    if isinstance(outcome, asyncio.CancelledError):
                        output = f"{name}: cancelled by parent before completion"
                    elif isinstance(outcome, BaseException):
                        output = (f"{type(outcome).__name__} while calling "
                                  f"{name}: {outcome}")
                    else:
                        _name, output = outcome
                    # The tool-result pre-check (payload_guard →
                    # _core.preview, 2026-11-12): an oversized result
                    # ships as a _preview ENVELOPE (structure, column
                    # stats, head/mid/tail batches, how_to_read) — a
                    # wide db_run_query dump (500 rows is a row cap,
                    # not a byte cap) would otherwise ride every later
                    # turn of this hop; the LLM reads the metadata and
                    # decides what to fetch. The breaker keeps the RAW
                    # output (its exit_code/stderr sniff needs the
                    # JSON, not the envelope).
                    raw_output = output
                    # THE REPEAT GUARD (2026-11-13, run 4597f052): the
                    # identical call, unchanged, right after a failure —
                    # stamp the anti-repeat teaching onto the result
                    # (the failure classifiers live below, on raw_output).
                    sig = (name, str((call.get("function") or {})
                                     .get("arguments") or ""))
                    if (sig == self._prev_call_sig
                            and self._prev_call_failed):
                        output = (output + "\n[Repeat guard] this "
                                  "IDENTICAL call just failed — the "
                                  "result above names why. Change the "
                                  "arguments (or the approach); "
                                  "resending it unchanged repeats the "
                                  "same failure.")
                        logger.info("step %d/%d: repeat guard stamped "
                                    "on %s", step, self._max_steps, name)
                    self._prev_call_sig = sig
                    self._prev_call_failed = self._tool_output_failed(
                        name, raw_output)
                    try:
                        output = await self._preview_tool_output(name,
                                                                 output)
                    except Exception:  # noqa: BLE001 — fail-soft seam
                        output = guard_text(raw_output)
                    tool_calls_log.append(
                        {"step": step, "name": name, "output": output})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id") or "",
                        "content": output,
                    })
                    self._note_sandbox_outcome(call, name, raw_output)
                # THE DETERMINISTIC HANDBACK RESERVE (2026-11-13, run
                # 4597f052): scripting spawns died at the step cap AFTER
                # their work was done — the evidence sat in ticked plan
                # nodes (2825's window note, 2889's two ticks) while the
                # handback never landed, so the parent re-spawned the
                # same study. The jev join read is affirm-only and a
                # node doing orderly bookkeeping reads as "not messy",
                # so the budget clause alone never acted. One step
                # before the cap this node is told — no model judgment,
                # pure budget arithmetic — to hand back INSTEAD of
                # starting new work; the forced-join lane above then
                # converts a still-calling final reply into the joined
                # answer, and a text reply ships through the normal
                # answer gate. Main-path nodes are untouched: the cap
                # RuntimeError stays their semantics (the step lane
                # settles + the supervisor diagnoses).
                if ((self._node_role == "scripting" or self._fork_child)
                        and not self._join_nudged
                        and step == self._max_steps - 1):
                    self._join_nudged = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Handback reserve] ONE step remains. Do not "
                            "start new tool work: write your HANDBACK now "
                            "in one message — what you established (the "
                            "key numbers/notes), what is still broken (the "
                            "exact error), the script path in the shared "
                            "sandbox, and what the parent should do next. "
                            "This message becomes this node's result in "
                            "the parent chain."
                        ),
                    })
                # The write-run-fix loop breaker, two lanes — the
                # 2026-11-11 run-e1c366bd failure mode (nine whole-script
                # rewrites chasing eight SQL bugs until the user stopped
                # the run): (1) the FRESH-CONTEXT ESCALATION — after
                # sandbox_spawn_after_failures consecutive traceback-class
                # script failures, spawn ONE repair subagent whose whole
                # context is the compact handoff and whose result the
                # parent awaits; (2) the bounded nudge for everything the
                # spawn lane does not cover.
                limits = run_limits()
                spawned = None
                if limits.sandbox_spawn_after_failures > 0 \
                        and step < self._max_steps:
                    spawned = await self._maybe_spawn_repair(goal, limits)
                if spawned is not None:
                    messages.append({"role": "user", "content": spawned})
                elif self._sandbox_fail_streak \
                        >= limits.sandbox_failure_nudge_threshold \
                        and self._sandbox_nudges_used \
                        < limits.sandbox_failure_max_nudges:
                    failed = self._sandbox_fail_streak
                    self._sandbox_nudges_used += 1
                    self._sandbox_fail_streak = 0
                    messages.append({
                        "role": "user",
                        "content": (
                            f"The last {failed} sandbox attempts failed "
                            "— you are repeating a write-run-fix cycle. "
                            "Stop rewriting the whole script: read the "
                            "traceback's LAST line, patch the one broken "
                            "block with sandbox_edit_file, and run each "
                            "block under its own try/except that records "
                            "the error and continues — one failing query "
                            "must not discard the rest of the evidence. "
                            "If it still fails, split the work into "
                            "smaller scripts or fall back to "
                            "db_run_query."
                        ),
                    })
        finally:
            # No orphan spawns: whichever way the loop ends (answer, step
            # limit, cancellation, crash) still-running background tasks
            # are stopped here — collected ones are done by definition.
            for run in self._subagent_runs:
                if (run.background and run.task is not None
                        and not run.task.done()):
                    run.task.cancel()

        raise RuntimeError(
            f"deep agent hit the {self._max_steps}-step limit without a "
            "final answer; narrow the goal or raise --max-steps")

    # The sandbox tools whose outcomes the write-run-fix breaker tracks
    # (the observed pathology rewrites files and re-runs scripts — reads
    # and listings ride along as hard-error evidence only).
    _SANDBOX_TOOL_NAMES = frozenset((
        "sandbox_run", "sandbox_write_file", "sandbox_read_file",
        "sandbox_edit_file", "sandbox_list_files"))

    async def _preview_tool_output(self, name: str, output: str) -> str:
        """The oversized tool result → ``_preview`` envelope (the
        loop seam of the metadata+preview layer, 2026-11-12). A
        tabular result (the readonly daemon's {columns, rows}, a
        JSON-table sandbox stdout) takes the DATA branch (structure,
        per-column stats, head/mid/tail batches); everything else the
        TEXT branch (char slices), with the soft jev classification
        (sentiment / domain / industries — the injected engine, the
        ``preview_jev_classify`` knob, the ``JEV_PREVIEW=0``
        kill-switch) riding the envelope when it made a read.
        how_to_read names the fetch path so the model decides what to
        read. Under the entry cap nothing changes; any envelope
        failure falls back to guard_text (the caller also catches)."""
        limits = run_limits()
        if len(output) <= limits.payload_entry_max_chars:
            return output
        from llm_agents._core.preview import PreviewBuilder

        source = ("db" if name.startswith("db_")
                  else "sandbox" if name.startswith("sandbox")
                  else "tool")
        how_to_read = {
            "db": "do NOT re-fetch this data into the conversation — "
                  "write a sandbox script (study_kit db.query) filtered "
                  "on the table's key columns that AGGREGATES/derives "
                  "and prints only the small computed result, then "
                  "sandbox_run it",
            "sandbox": "the full output stays in the sandbox session — "
                       "extend the script with filters/aggregation and "
                       "print only the small computed slice",
            "tool": "re-run the tool with narrower arguments",
        }.get(source, "re-run the tool with narrower arguments")
        builder = PreviewBuilder(
            preview_batch_rows=limits.preview_batch_rows,
            preview_chars=limits.payload_preview_chars,
            stats_sample_rows=limits.preview_stats_sample_rows)
        envelope = builder.build(source, output, how_to_read)
        if envelope.kind == "text":
            envelope = await self._classify_envelope(envelope, limits)
        return envelope.as_string()

    async def _classify_envelope(self, envelope, limits):
        """The jev content reads over a TEXT envelope (sentiment /
        domain / industries). 2026-11-13: the read RESULTS no longer
        ride the envelope into the conversation (jev decisions stay out
        of LLM context) — each read records itself as a decision step
        (llm.deep_agent_aux_steps, via the engine's _read seam) and the
        argmax line logs. A missing engine, a disabled knob or a failed
        read is a no-op — the classification can never alter a
        result."""
        import os
        if (not limits.preview_jev_classify
                or self._decision_engine is None
                or os.environ.get("JEV_PREVIEW", "").strip().lower()
                in ("0", "false", "off")):
            return envelope
        head = envelope.text_batches.get("head", "")
        if not head:
            return envelope
        try:
            # The industry candidates resolve in the langgraph package
            # (the DB-reading half of the classification) — core passes
            # the text through the duck-typed engine only; the resolver
            # import here is the ONE sanctioned seam crossing (same
            # discipline as the spawn gate's injected engine).
            from llm_agents.deep_agents.langgraph.industry_candidates \
                import RESOLVER
            candidates = await RESOLVER.candidates_for_async(head)
            decisions = await self._decision_engine.decide_content(
                text_head=head[:1200], total_chars=envelope.total_chars,
                source=envelope.source, industry_candidates=candidates)
            parts = [
                f"{key}={d.action} p={d.confidence:.2f}"
                for key, d in decisions.items()
                if getattr(d, "ok", False) and getattr(d, "action", None)]
            logger.info("preview classification (off-context): %s",
                        ", ".join(parts) if parts else "no decisive read")
        except Exception:  # noqa: BLE001 — fail-soft enrichment
            logger.info("preview classification unavailable (%s)",
                        envelope.note[:120])
        return envelope

    def _tool_output_failed(self, name: str, output: str) -> bool:
        """The repeat guard's failure classifier (2026-11-13): the
        tool-layer error texts all announce themselves in the first
        line ("MCP tool error: …", "<tool> error: …", "old_text not
        found in …"); a sandbox_run fails on its JSON exit_code != 0
        or a timeout — the same shapes the breaker reads."""
        if not isinstance(output, str) or not output:
            return False
        head = output[:240]
        low = head.lower()
        if (low.startswith("mcp tool error:")
                or low.startswith(f"{name.lower()} error:")
                or low.startswith("old_text not found")):
            return True
        at = head.find('"exit_code"')
        if at >= 0:
            try:
                code = int(head[at:].split(":", 1)[1].split(",")[0])
                if code != 0:
                    return True
            except (ValueError, IndexError):
                pass
        return '"timed_out": true' in low

    def _note_sandbox_outcome(self, call: Dict[str, Any], name: str,
                              output: str) -> None:
        """Track consecutive failed sandbox-tool outcomes for the loop
        breaker. A hard error is the tool-error / cancel / exception
        text any sandbox tool can return; a ``sandbox_run`` also fails
        on its JSON ``exit_code != 0`` (the script's own traceback —
        the observed rewrite-loop shape). A successful ``sandbox_run``
        resets the streaks; successful writes/edits are neutral
        progress, not resets. Non-sandbox tools never count. Along the
        way it captures the failing script's path and, per
        traceback-class failure, the exception's LAST line — the spawn
        handoff's fix history."""
        if name not in self._SANDBOX_TOOL_NAMES:
            return
        try:
            path = ((json.loads((call.get("function") or {})
                                .get("arguments") or "{}") or {})
                    .get("path"))
            if isinstance(path, str) and path:
                self._sandbox_script_path = path
        except ValueError:
            pass
        if name == "sandbox_run" and '"exit_code"' in output:
            if '"exit_code": 0' in output:
                self._sandbox_fail_streak = 0
                self._sandbox_syntax_streak = 0
            else:
                self._sandbox_fail_streak += 1
                if "Traceback" in output or "SyntaxError" in output:
                    self._sandbox_syntax_streak += 1
                    self._remember_fail_line(output)
            return
        first_line = output.split("\n", 1)[0]
        if (output.startswith("MCP tool error")
                or output.startswith(f"{name} error:")
                or output.startswith(f"{name}: cancelled")
                or " while calling " in first_line):
            self._sandbox_fail_streak += 1

    def _remember_fail_line(self, output: str) -> None:
        """One fix-history entry: the failed run's LAST exception line
        (the JSON-embedded stderr's tail; the capped-output fallback is
        the raw tail). At most the last eight are kept."""
        line = ""
        try:
            stderr = str((json.loads(output) or {}).get("stderr") or "")
            lines = [s.strip() for s in stderr.splitlines() if s.strip()]
            line = lines[-1] if lines else ""
        except ValueError:
            line = output.strip()[-200:]
        line = line[:200]
        if line:
            self._sandbox_fail_lines.append(line)
            del self._sandbox_fail_lines[:-8]

    async def _maybe_spawn_repair(self, goal: str,
                                 limits) -> Optional[str]:
        """The breaker's fresh-context escalation: after
        ``sandbox_spawn_after_failures`` consecutive traceback-class
        script failures, spawn ONE repair subagent — FOREGROUND, the
        parent awaits it — whose whole context is the compact handoff
        (run goal + script path + fix history) instead of this
        conversation's grown transcript. The child state row registers
        under the current step through the same input hook a
        model-called spawn rides; the injection message below carries
        the child's answer back. Returns the injection user message,
        or None when the trigger does not apply (budget spent, depth
        cap, no sandbox/subagents) — then the nudge lane still
        guards."""
        if self._sandbox_syntax_streak < limits.sandbox_spawn_after_failures:
            return None
        if self._sandbox_spawns_used >= limits.sandbox_spawn_max:
            return None
        if (not self._sandbox or not self._allow_subagents
                or self._spawn_depth >= limits.max_spawn_depth):
            return None
        # THE SYSTEM ONE LANE (decision/, 2026-11-11): when an engine is
        # injected, the calibrated jev read judges whether THIS failure
        # pattern is one a fresh-context child fixes — a confident NO
        # keeps the nudge lane judging (the code gates above stay hard;
        # an unavailable or below-floor read spawns, preserving the
        # breaker's default behavior).
        if self._decision_engine is not None:
            # Primitive kwargs only — core imports nothing from
            # langgraph.decision (the layering rule); the injected
            # engine builds its own typed facts.
            decision = await self._decision_engine.decide_spawn(
                syntax_streak=self._sandbox_syntax_streak,
                threshold=limits.sandbox_spawn_after_failures,
                spawns_used=self._sandbox_spawns_used,
                max_spawns=limits.sandbox_spawn_max,
                script_path=self._sandbox_script_path or "",
                fail_lines=" | ".join(self._sandbox_fail_lines[-4:]),
            )
            if decision.ok and decision.action == "no":
                logger.info(
                    "sandbox repair spawn declined by the jev gate "
                    "(p=%.2f)", decision.confidence)
                return None
        self._sandbox_spawns_used += 1
        text = await self._run_subagent({
            "goal": self._repair_handoff(goal), "background": False,
            "role": "scripting"})
        if text.startswith(("spawn_subagent error:",
                            "spawn_subagent rejected by policy:",
                            "subagent input-hook failed:")):
            # The delegation path refused the spawn — spend nothing; the
            # streak stays and the nudge lane guards instead.
            self._sandbox_spawns_used -= 1
            return None
        self._sandbox_syntax_streak = 0
        self._sandbox_fail_streak = 0
        text = guard_text(text)
        logger.info("sandbox repair spawn returned (%d chars)", len(text))
        return (f"[Fresh-context repair agent took over] A subagent "
                f"continued the failed script from a clean context and "
                f"returned:\n\n{text}")

    def _repair_handoff(self, goal: str) -> str:
        """The repair spawn's whole context — deliberately COMPACT (the
        token-saving point): the run goal, the script path (the shared
        sandbox session), the failed runs' exception lines and the
        method. Never the parent transcript."""
        path = self._sandbox_script_path or "scripts/<the failing script>"
        tried = ""
        if self._sandbox_fail_lines:
            tried = ("\nAlready tried (%d failed runs — do NOT repeat "
                     "these fixes): %s"
                     % (len(self._sandbox_fail_lines),
                        " | ".join(self._sandbox_fail_lines)))
        return (
            "[Repair handoff — fresh context] The parent conversation's "
            "script-debugging loop is burning its context budget; you "
            "take over with a clean slate. Your answer IS the handback: "
            "return the script's KEY EVIDENCE (the results it prints, "
            "concise) — the parent uses it directly.\n"
            f"Run goal: {goal}\n"
            f"Script: {path} — it lives in the SHARED sandbox session: "
            "sandbox_read_file it first; NEVER rewrite it whole — "
            "patch the one broken block with sandbox_edit_file."
            + tried +
            "\nMethod: sandbox_run it → read the traceback's LAST line "
            "→ sandbox_edit_file the ONE broken block → re-run. Keep "
            "per-block try/except that records errors and continues — "
            "one bad query must not discard the rest of the evidence. "
            "If the script's approach itself is wrong, fix the "
            "approach; if the data is missing, say so in the answer.")

    # The sandbox tools whose PROPOSAL marks a response as coding work
    # (the router's spawn pre-filter — reads/listings are not script
    # writing). A SUBSET of _SANDBOX_TOOL_NAMES.
    _ROUTABLE_SANDBOX_TOOLS = frozenset((
        "sandbox_run", "sandbox_write_file", "sandbox_edit_file"))

    async def _engine_read(self, method: str, **kwargs):
        """One duck-typed engine read, MISSING-METHOD-SAFE: an injected
        engine that predates the router (a probe's read recorder, an
        old stub) carries no decide_route_* / decide_failure_probe —
        the lane treats that as "no read available" (None), never a
        crash; any read exception lands the same way (the engine's own
        never-raise contract, defended here too)."""
        handler = getattr(self._decision_engine, method, None)
        if handler is None:
            return None
        try:
            return await handler(**kwargs)
        except Exception as exc:  # noqa: BLE001 — the lane never blocks
            logger.info("router engine read %s failed: %s", method, exc)
            return None

    def _route_env_off(self) -> bool:
        """The router's env kill-switch (JEV_ROUTE=0) — probes that
        target the UN-ROUTED lanes set it, the JEV_DECISIONS=0 pattern's
        sibling."""
        import os
        return os.environ.get("JEV_ROUTE", "").strip().lower() in (
            "0", "false", "off")

    def _fork_point_candidates(
            self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The drift fork's checkpoint candidates (2026-11-13): every
        assistant turn that STATED something (non-empty content — pure
        tool-call turns carry no pickable context), as a self-consistent
        checkpoint — its cut index sits AFTER that turn's own tool
        results, so the inherited prefix ends protocol-clean. Returns
        the ≤16 most recent (the jev per-request option ceiling
        reserves room for the 'now' option); each entry carries its
        ``label`` (turn_<msg_index> — the read's answer token), the
        capped ``head`` the read conditions on, and the ``cut`` (the
        transcript length to inherit through)."""
        checkpoints: List[Dict[str, Any]] = []
        for i, m in enumerate(messages):
            if str(m.get("role")) != "assistant":
                continue
            head = str(m.get("content") or "").strip()
            if not head:
                continue
            j = i + 1
            if m.get("tool_calls"):
                while j < len(messages) \
                        and str(messages[j].get("role")) == "tool":
                    j += 1
            checkpoints.append({"label": f"turn_{i}",
                                "head": head[:160], "cut": j})
        return checkpoints[-16:]

    async def _route_response(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]], step: int,
            messages: Optional[List[Dict[str, Any]]] = None,
            ) -> Optional[str]:
        """THE SYSTEM ONE ROUTER (2026-11-12): judge whether the NEXT
        work this response proposes continues this conversation's
        inference chain ('' — continue), is a self-contained coding
        sub-assistant task ('spawn' — a fresh scripting sub node takes
        over, keeping this node on its biz reasoning), a CONTINUATION
        worth delegating to an inherit-context child ('fork',
        2026-11-13 — the sub node continues from this conversation's
        full transcript while the parent awaits), or — in a scripting
        node whose debug loop is messy — should stop the node ('join' —
        hand back to the parent chain). CODE FILTERS FIRST:
        the spawn and fork reads fire only when the response PROPOSES
        sandbox write/run/edit calls (mechanical coding detection) in a
        non-scripting node with sandbox room and each lane's own
        budget; the join read
        only in a scripting node showing messiness (failed sandbox
        outcomes or a mostly-spent budget). Every read is
        AFFIRM-ONLY — a confident yes acts, everything else (no read,
        host down, below floor) continues the model's own plan."""
        if self._decision_engine is None:
            return None
        limits = run_limits()
        if not limits.jev_route or self._route_env_off():
            return None
        names = [str((c.get("function") or {}).get("name") or "")
                 for c in calls]
        sandbox_calls = sum(1 for n in names
                            if n in self._ROUTABLE_SANDBOX_TOOLS)
        # SPAWN lane: proposed coding work in a node that is NOT itself
        # scripting (the no-re-spawn rule) — the parent stays biz.
        if (sandbox_calls > 0
                and self._node_role != "scripting"
                and limits.jev_route_max_spawns > 0
                and self._route_spawns_used < limits.jev_route_max_spawns
                and self._sandbox is not None and self._can_spawn):
            decision = await self._engine_read(
                "decide_route_spawn",
                node_role=self._node_role, depth=self._spawn_depth,
                steps_used=step, max_steps=self._max_steps,
                response_head=str(message.get("content") or "")[:400],
                pending_tools=",".join(names)[:200],
                sandbox_calls=sandbox_calls,
                fail_streak=self._sandbox_fail_streak,
                syntax_streak=self._sandbox_syntax_streak,
                spawns_used=self._route_spawns_used,
                max_spawns=limits.jev_route_max_spawns)
            if decision.ok and decision.action == "yes":
                logger.info("step %d/%d: router spawns the coding to a "
                            "scripting sub node (p=%.2f)", step,
                            self._max_steps, decision.confidence)
                return "spawn"
        # FORK / DRIFT lane (2026-11-13, reframed): the drift watch —
        # after EVERY tool-calling response in a non-scripting node
        # with fork budget, jev judges whether the latest context has
        # GRADUALLY DRIFTED from the node's purpose (goal_head); a
        # confident yes is followed by the FORK-POINT read deciding
        # from which previous LLM context the fork continues (an
        # earlier, pre-drift checkpoint; 'now'/below-floor/unknown →
        # fork-from-now, the safe default). Everything after the cut is
        # ABANDONED — the hooks materialize its stub row (child kind 2)
        # and the UI demotes it to the right-side edge path. Affirm-only
        # twice: any no / below-floor / unavailable read continues the
        # node's own plan.
        if (self._node_role != "scripting"
                and limits.jev_route_fork
                and limits.jev_route_max_forks > 0
                and self._route_forks_used < limits.jev_route_max_forks
                and self._sandbox is not None and self._can_spawn):
            decision = await self._engine_read(
                "decide_route_fork",
                node_role=self._node_role, depth=self._spawn_depth,
                goal_head=str(goal or "")[:400],
                steps_used=step, max_steps=self._max_steps,
                response_head=str(message.get("content") or "")[:400],
                pending_tools=",".join(names)[:200],
                sandbox_calls=sandbox_calls,
                fail_streak=self._sandbox_fail_streak,
                syntax_streak=self._sandbox_syntax_streak,
                spawns_used=self._route_forks_used,
                max_spawns=limits.jev_route_max_forks)
            if decision.ok and decision.action == "yes":
                candidates = self._fork_point_candidates(messages)
                cut = len(messages)
                if candidates:
                    point = await self._engine_read(
                        "decide_route_fork_point",
                        goal_head=str(goal or "")[:400],
                        candidates=[{"label": c["label"],
                                     "head": c["head"]} for c in candidates],
                        steps_used=step, max_steps=self._max_steps)
                    if point is not None and point.ok:
                        chosen = (point.action or "").strip()
                        match = next((c for c in candidates
                                      if c["label"] == chosen), None)
                        if match is not None:
                            cut = match["cut"]
                n_abandoned = len(messages) - cut
                self._pending_fork_cut = cut
                self._pending_fork_note = (
                    f"[drift fork] the {n_abandoned} turn(s) after the "
                    f"pre-drift checkpoint were abandoned as off-purpose; "
                    f"their last statement read: "
                    f"{str(message.get('content') or '').strip()[:200]}")
                logger.info(
                    "step %d/%d: router drift-forks from checkpoint "
                    "cut=%d (%d later turn(s) abandoned) (p=%.2f)",
                    step, self._max_steps, cut, n_abandoned,
                    decision.confidence)
                return "fork"
        # JOIN lane: a scripting node gone messy — stop it and join the
        # parent chain (the parent's failure handling takes over).
        messy = (self._sandbox_fail_streak > 0
                 or self._sandbox_syntax_streak > 0
                 or step >= (self._max_steps * 3) // 5)
        if (self._node_role == "scripting" and limits.jev_route_join
                and messy and not self._join_nudged):
            decision = await self._engine_read(
                "decide_route_join",
                node_role=self._node_role,
                steps_used=step, max_steps=self._max_steps,
                response_head=str(message.get("content") or "")[:400],
                pending_tools=",".join(names)[:200],
                fail_streak=self._sandbox_fail_streak,
                syntax_streak=self._sandbox_syntax_streak)
            if decision.ok and decision.action == "yes":
                logger.info("step %d/%d: router joins this scripting "
                            "node back to the parent (p=%.2f)", step,
                            self._max_steps, decision.confidence)
                return "join"
        return None

    async def _spawn_scripting(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]]) -> Optional[str]:
        """The router's SPAWN action: spawn ONE scripting sub node
        FOREGROUND (the parent awaits) whose whole context is the
        SCRIPTING HANDOFF — never the parent transcript — and fold its
        answer back as the injection user message. Returns None when
        the delegation path refuses (the caller falls through to the
        model's own calls)."""
        limits = run_limits()
        self._route_spawns_used += 1
        text = await self._run_subagent({
            "goal": self._scripting_handoff(goal, message, calls),
            "background": False, "role": "scripting"})
        if text.startswith(("spawn_subagent error:",
                            "spawn_subagent rejected by policy:",
                            "subagent input-hook failed:")):
            self._route_spawns_used -= 1
            return None
        text = guard_text(text)
        logger.info("router scripting spawn returned (%d chars)", len(text))
        return (f"[Scripting sub node took over] A sub node ran the "
                f"coding work this step proposed (clean context, "
                f"shared sandbox) and returned:\n\n{text}")

    def _scripting_handoff(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]]) -> str:
        """The scripting spawn's whole context — COMPACT (the
        fresh-context point): the run goal, the response's own plan
        text, the proposed sandbox calls verbatim (capped) and the
        method. Never the parent transcript."""
        head = str(message.get("content") or "").strip()[:1200]
        call_lines: List[str] = []
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if name in self._ROUTABLE_SANDBOX_TOOLS:
                call_lines.append(
                    f"- {name}({str(function.get('arguments') or '')[:400]})")
        return (
            "[Scripting handoff — fresh context] A research "
            "conversation proposed the coding work below; you are the "
            "scripting sub node that takes it over. Your answer IS the "
            "handback: return the script's KEY EVIDENCE (the results "
            "it prints, concise and quantitative) plus, when something "
            "failed, the exact error and what is still missing — the "
            "parent uses it directly.\n"
            f"Run goal: {goal}\n"
            + (f"The proposing step's plan:\n{head}\n" if head else "")
            + "Proposed tool calls:\n" + "\n".join(call_lines)
            + "\nMethod: the sandbox session is SHARED — write/run "
            "scripts there (sandbox_write_file / sandbox_run), keep "
            "per-block try/except that records errors and continues, "
            "read the traceback's LAST line before patching, and "
            "aggregate/derive in pandas rather than one SQL monolith. "
            "Stay on the scripting — do not re-open the business "
            "question.")

    async def _fork_scripting(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]],
            transcript: List[Dict[str, Any]],
            cut: Optional[int] = None,
            drift_note: Optional[str] = None) -> Optional[str]:
        """The router's DRIFT-FORK action (2026-11-13): fork ONE
        inherit-context sub node FOREGROUND (the parent awaits) that
        continues this conversation from the chosen PRE-DRIFT
        checkpoint — the deep-copied transcript PREFIX rides into the
        child (``_run_subagent``'s inherit lane), everything after the
        cut is abandoned (``fork_abandoned_note`` → the hooks'
        kind-2 stub row) — and its answer folds back as the injection
        user message. Returns None when the delegation path refuses
        (the caller falls through to the model's own calls, the budget
        already refunded)."""
        self._route_forks_used += 1
        if cut is None or cut > len(transcript):
            cut = len(transcript)
        abandoned = cut < len(transcript)
        text = await self._run_subagent(
            {"goal": self._fork_handoff(goal, message, calls,
                                        abandoned=abandoned),
             "background": False, "role": "scripting"},
            inherit_transcript=transcript[:cut],
            fork_abandoned_note=drift_note if abandoned else None,
            tool_name=FORK_TOOL_NAME)
        if text.startswith((
                "spawn_subagent error:", "spawn_subagent rejected by "
                "policy:", "subagent input-hook failed:",
                "fork_subagent error:", "fork_subagent rejected by "
                "policy:")):
            self._route_forks_used -= 1
            return None
        text = guard_text(text)
        logger.info("router drift fork returned (%d chars)", len(text))
        mode = ("pre-drift context inherited — the abandoned tail was "
                "discarded" if abandoned else "full context inherited")
        return (f"[Fork sub node took over] A fork of this conversation "
                f"({mode}, shared sandbox) recovered the node's purpose "
                f"and returned:\n\n{text}")

    def _fork_handoff(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]],
            abandoned: bool = False) -> str:
        """The drift fork's directive: the child inherits the PRE-DRIFT
        context (or the whole conversation on fork-from-now), so the
        goal re-anchors the NODE'S PURPOSE, names the abandoned
        direction (context only — never to resume) and carries the
        latest response's proposed calls verbatim (capped)."""
        head = str(message.get("content") or "").strip()[:1200]
        call_lines: List[str] = []
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if name in self._ROUTABLE_SANDBOX_TOOLS:
                call_lines.append(
                    f"- {name}({str(function.get('arguments') or '')[:400]})")
        return (
            "[Fork continuation — drift recovery] The conversation you "
            "inherited is this node's context as of a pre-drift "
            "checkpoint"
            + (" — its later turns drifted off-purpose and were "
               "ABANDONED (not inherited); do NOT resume that "
               "direction." if abandoned else ".") + " The node's "
            "PURPOSE you must complete: " + goal + "\n"
            "Your answer IS the handback: return the KEY EVIDENCE "
            "(concise and quantitative) plus, when something failed, "
            "the exact error and what is still missing — the parent "
            "uses it directly and has NOT seen anything you do here.\n"
            + (f"The abandoned tail's last statement (context only — "
               f"do not resume it):\n{head}\n" if head and abandoned
               else (f"The proposing step's plan:\n{head}\n"
                     if head else ""))
            + ("Proposed tool calls (the drift watch intercepted them; "
               "run them only as far as they serve the purpose):\n"
               + "\n".join(call_lines) if call_lines else "")
            + "\nMethod: the sandbox session is SHARED and its files "
            "are already yours — continue the established scripts "
            "rather than rewriting them, keep per-block try/except, "
            "read the traceback's LAST line before patching, and stay "
            "on the purpose — do not re-open or re-derive settled "
            "work.")

    async def _maybe_probe_failure(
            self, run: SubagentRun, text: str) -> str:
        """The FAILURE-PROBE lane (2026-11-12): a scripting child just
        FAILED and joined back — spawn ONE INVESTIGATOR sub node to find
        out WHY, authorized with the failed node's exact references
        (state id, llm chain id, goal, error) to inspect through
        search_thread_context and the shared sandbox. STRONGLY
        PROMPTED, the inverse of the affirm-only lanes: the jev read
        spawns on EVERY outcome except a confident NO (an unavailable or
        below-floor read still spawns — bounded by the probe budget).
        The investigator's findings append to the tool text the parent
        model reads."""
        limits = run_limits()
        if (self._decision_engine is None or not limits.jev_route
                or limits.jev_route_probe_max <= 0
                or self._route_probes_used >= limits.jev_route_probe_max
                or not self._can_spawn or self._route_env_off()):
            return text
        decision = await self._engine_read(
            "decide_failure_probe",
            failed_role=run.request.role or "scripting",
            failed_goal=run.request.goal[:400],
            error_text=str(run.error or text)[:400],
            probes_used=self._route_probes_used,
            max_probes=limits.jev_route_probe_max)
        if decision is None:
            return text  # the lane has no read path — no probe
        if decision.ok and decision.action == "no":
            logger.info("failure probe declined by the jev gate "
                        "(p=%.2f)", decision.confidence)
            return text
        self._route_probes_used += 1
        probe_text = await self._run_subagent({
            "goal": self._probe_handoff(run), "background": False,
            "role": "investigator"})
        if probe_text.startswith(("spawn_subagent error:",
                                  "spawn_subagent rejected by policy:",
                                  "subagent input-hook failed:")):
            self._route_probes_used -= 1
            return text
        probe_text = guard_text(probe_text)
        logger.info("failure probe investigator returned (%d chars)",
                    len(probe_text))
        return (f"{text}\n\n[Failure probe — an investigator sub node "
                f"examined why] {probe_text}")

    def _probe_handoff(self, run: SubagentRun) -> str:
        """The investigator's whole context: the failed node's goal and
        error plus its EXACT references — the parent's AUTHORIZATION.
        The chain-scoped search_thread_context the investigator carries
        reaches the failed node's recorded llm traffic by chain id, and
        the shared sandbox session holds its script."""
        refs: List[str] = []
        if run.state_id is not None:
            refs.append(f"- node state id: {run.state_id} (its row in "
                        "llm.deep_agent_states / the thread graph)")
        if run.llm_context_id is not None:
            refs.append(f"- llm request chain id: {run.llm_context_id} "
                        "(search_thread_context hits its recorded "
                        "requests — filter the results on it)")
        return (
            "[Failure probe — you are the investigator] A scripting "
            "sub node of this run FAILED and the parent still needs "
            "the work. Find out WHY it failed — the root cause, not "
            "the symptom — and what the parent should do differently. "
            "You are AUTHORIZED to inspect exactly these:\n"
            f"Failed goal: {run.request.goal[:600]}\n"
            f"Recorded error: {str(run.error or '?')[:400]}\n"
            + ("\n".join(refs) + "\n" if refs else "")
            + "- the SHARED sandbox session (sandbox_read_file the "
            "failed node's script; never rewrite it)\n"
            "Use search_thread_context for the recorded traffic (the "
            "run's whole chain is in scope — the references above name "
            "the failed node's chain), read the last exception line, "
            "and answer concisely: root cause / what to change / "
            "whether a re-run with the fix is worth it.")

    def _uncollected_spawns(self) -> List[SubagentRun]:
        """Background spawns whose result the model has not gathered —
        the final-answer gate's set (running OR finished-uncollected)."""
        return [run for run in self._subagent_runs
                if run.background
                and run.run_id not in self._background_collected]

    async def run_delegation(self, goal: str, *,
                             max_steps: Optional[int] = None,
                             ) -> str:
        """Run ONE delegated sub-goal through the spawner — no main
        conversation at all. The thread runner's step-graph engine calls
        this for a plan step that carries a delegation goal: the child
        runs under the full supervision policy (hooks → registry →
        semaphore → timeout → output hook) and its result text (or
        failure text — nothing here raises) becomes the step's finding.
        The agent instance is never ``run()`` in this mode; its registry
        still records the child (``subagent_runs()``)."""
        return await self._run_subagent({
            "goal": goal,
            **({"max_steps": int(max_steps)} if max_steps else {}),
        })

    # ------------------------------------------------------------------
    # One tool call: MCP tool or the built-in spawner
    # ------------------------------------------------------------------

    async def _execute_call(self, call: Dict[str, Any],
                            step: int, *,
                            reply_cut: bool = False,
                            transcript: Optional[List[Dict[str, Any]]] = None,
                            ) -> Tuple[str, str]:
        """Run one tool call; return (tool name, result text for the LLM).
        ``reply_cut``: this response ended on the per-request output
        ceiling (finish_reason 'length') — a cut arguments string is
        the ceiling's fault, and the error text teaches the resend
        remedy instead of a bare JSON parse error. ``transcript`` —
        this conversation's messages so far, read ONLY by the fork
        spawner (the fork child deep-copies it; the list itself is
        never mutated)."""
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be a JSON object")
        except ValueError as exc:
            if reply_cut:
                return (name,
                        f"MCP tool error: bad arguments for {name}: {exc} "
                        "— your previous reply hit the max output tokens "
                        "mid tool-call and the arguments JSON was cut. "
                        "Resend the SAME call with SHORTER arguments: "
                        "split a large file write into blocks, or patch "
                        "one block with sandbox_edit_file instead of "
                        "resending the whole content.")
            return name, f"MCP tool error: bad arguments for {name}: {exc}"
        if name == SUBAGENT_TOOL_NAME:
            logger.info("step %d/%d: spawn subagent(%s)", step,
                        self._max_steps,
                        json.dumps(arguments, ensure_ascii=False))
            return name, await self._run_subagent(arguments)
        if name == FORK_TOOL_NAME:
            logger.info("step %d/%d: fork subagent(%s)", step,
                        self._max_steps,
                        json.dumps(arguments, ensure_ascii=False))
            return name, await self._run_fork_subagent(arguments,
                                                       transcript)
        if name == COLLECT_TOOL_NAME:
            logger.info("step %d/%d: collect_subagents(%s)", step,
                        self._max_steps,
                        json.dumps(arguments, ensure_ascii=False))
            return name, await self._collect_subagents(arguments)
        builtin = self._builtin_tools.get(name)
        if builtin is not None:
            logger.info("step %d/%d: builtin %s(%s)", step, self._max_steps,
                        name, json.dumps(arguments, ensure_ascii=False))
            try:
                return name, await builtin.handler(arguments)
            except Exception as exc:  # noqa: BLE001 — tool text, not a crash
                return name, f"{name} error: {type(exc).__name__}: {exc}"
        logger.info("step %d/%d: call %s(%s)", step, self._max_steps, name,
                    json.dumps(arguments, ensure_ascii=False))
        return name, await self._mcp.call_tool(name, arguments)

    # ------------------------------------------------------------------
    # The spawner: intercept -> register -> supervise -> intercept
    # ------------------------------------------------------------------

    async def _run_fork_subagent(
            self, arguments: Dict[str, Any],
            transcript: Optional[List[Dict[str, Any]]]) -> str:
        """The model-facing FORK (2026-11-13, ``fork_subagent``): the
        supervised delegation whose child INHERITS this conversation's
        transcript (deep-copied here; the parent's list is never
        aliased). Foreground by definition — a ``background`` argument
        is forced off (a detached fork would diverge from the parent's
        living copy) — and the error text wears the fork tool's name so
        the model reads its own lane."""
        return await self._run_subagent(
            {**arguments, "background": False},
            inherit_transcript=transcript,
            tool_name=FORK_TOOL_NAME)

    async def _run_subagent(
            self, arguments: Dict[str, Any],
            inherit_transcript: Optional[List[Dict[str, Any]]] = None,
            fork_abandoned_note: Optional[str] = None,
            tool_name: str = SUBAGENT_TOOL_NAME) -> str:
        """Supervise one delegation end to end; return its tool text.

        Policy is parent-side only: the hooks shape the request and the
        result, the semaphore bounds fan-out, the timeout bounds the
        child's whole life. Nothing here raises — every failure mode is
        tool-result text the parent model can react to.

        ``inherit_transcript`` — the FORK lane (2026-11-13): the parent
        transcript this child continues from (deep-copied HERE so the
        parent's own list can never alias into the child); None keeps
        the spawn semantics. ``fork_abandoned_note`` — the drift fork's
        abandoned-tail description (the hooks materialize its stub row);
        None = fork-from-now / a plain spawn. ``tool_name`` — the
        model-facing tool the caller rode in on (spawn_subagent |
        fork_subagent); the error prefixes wear it.
        """
        goal = str(arguments.get("goal") or "").strip()
        if not goal:
            return (f"{tool_name} error: 'goal' is required and "
                    "must be a non-empty string")
        requested = arguments.get("max_steps")
        max_steps = self._max_steps
        if isinstance(requested, int) and requested >= 1:
            max_steps = min(requested, self._max_steps)
        background = arguments.get("background") is True
        # The node role — a HARNESS stamp (the router's scripting
        # handoff, the repair spawn, the failure probe); the model's own
        # spawn arguments carry none and stay ''.
        role = str(arguments.get("role") or "").strip()
        run = SubagentRun(
            run_id=uuid.uuid4().hex[:8],
            request=SubagentRequest(
                goal=goal, max_steps=max_steps, background=background,
                depth=self._spawn_depth + 1, role=role,
                inherit_transcript=(copy.deepcopy(inherit_transcript)
                                    if inherit_transcript is not None
                                    else None),
                fork_abandoned_note=fork_abandoned_note),
            background=background,
        )

        # INPUT interception: rewrite or veto before anything starts.
        if self._subagent_input_hook is not None:
            try:
                await self._subagent_input_hook(run)
            except SubagentSpawnRejected as exc:
                return f"{tool_name} rejected by policy: {exc}"
            except Exception as exc:  # noqa: BLE001 — surfaced as tool text
                return (f"subagent input-hook failed: "
                        f"{type(exc).__name__}: {exc}")
        if not run.request.goal.strip():
            return (f"{tool_name} rejected by policy: the input "
                    "hook blanked the goal")
        goal = run.request.goal.strip()
        self._subagent_runs.append(run)

        # The child runs as a real Task so cancel_subagent() and the
        # timeout can control its lifecycle from outside the await.
        run.task = asyncio.ensure_future(self._execute_subagent(run))
        if run.background:
            # SPAWN: a detached supervisor applies the same timeout/output
            # interception and parks the finished result for collection —
            # the loop keeps moving, the answer gate holds until the
            # model gathers it.
            asyncio.ensure_future(self._supervise_background(run))
            return (f"subagent {run.run_id} spawned in the background "
                    f"(goal: {goal[:80]}) — keep working; call "
                    "collect_subagents (wait=true) when you need its "
                    "result, and keep the run id so a plan update can "
                    "stop the spawn if the plan changes")
        try:
            text = await asyncio.wait_for(run.task, timeout=self._subagent_timeout)
        except asyncio.TimeoutError:
            # wait_for cancelled the task; the child's own handler
            # marked it cancelled — this is the timed-out flavour.
            run.status = "timed_out"
            text = (f"subagent {run.run_id} timed out after "
                    f"{self._subagent_timeout:g}s and was stopped; "
                    "narrow the delegated goal or split it")
        text = await self._apply_output_hook(run, text)
        # The FAILURE-PROBE lane (2026-11-12): a failed scripting child
        # joins back — the parent may spawn an AUTHORIZED investigator
        # to learn why (strongly prompted, budget-bounded; the probe's
        # own failures never probe — the lane fires on the scripting
        # role only, so the recursion is structurally closed).
        if run.status in ("failed", "timed_out") \
                and run.request.role == "scripting":
            text = await self._maybe_probe_failure(run, text)
        return text

    async def _execute_subagent(self, run: SubagentRun) -> str:
        """The child coroutine: slot, run, record. Status bookkeeping
        lives HERE so it is correct under both timeout and external
        cancellation (the task is always awaited — by wait_for or the
        step's gather — so the record always settles)."""
        try:
            async with self._subagent_slots:
                run.status = "running"
                run.started_at = datetime.datetime.now(SHANGHAI_TZ)
                # The child's depth rung: one below this agent. Below
                # the cap it carries the spawn tool itself (a subagent
                # may spawn sub-sub agents when its investigation needs
                # it) and its prompt teaches it; at the cap the child
                # works its goal alone with the focused prompt.
                child = DeepAgent(
                        self._provider, self._mcp,
                        model=self._model, max_steps=run.request.max_steps,
                        system=(run.request.system + DELEGATION_GUIDANCE
                                if (self._allow_subagents
                                    and run.request.depth
                                    < run_limits().max_spawn_depth)
                                else run.request.system),
                        temperature=self._temperature,
                        allow_subagents=self._allow_subagents,
                        spawn_depth=run.request.depth,
                        # The child's NODE ROLE rides the request (the
                        # router's scripting children never re-spawn, the
                        # join lane watches them) and the decision engine
                        # threads down so the child's OWN responses route.
                        node_role=run.request.role,
                        decision_engine=self._decision_engine,
                        # THE FORK CHILD flag (2026-11-13): an inherit-
                        # context child shares the handback reserve's
                        # contract (the parent awaits it foreground).
                        fork_child=run.request.inherit_transcript
                        is not None,
                        # The spawn's OWN tools ride here — the runner's input
                        # hook mounted its spawn-scoped plan kit (update_plan /
                        # update_plan_step) beside the inherited sandbox.
                        builtin_tools=run.request.builtin_tools,
                # Scratch space is shared with the parent: files
                # the parent wrote are readable here and vice versa.
                sandbox=self._sandbox,
                # The DB-resolved tool descriptions propagate so the
                # child's inherited sandbox tools carry the same
                # overrides (its spawn kit applied its own at build).
                tool_prompts=self._tool_prompts,
                # The WHOLE supervision policy propagates: the same
                # timeout and concurrency cap, plus the child's OWN
                # hooks (stamped by the parent-side input hook) so the
                # child's spawns register one level deeper. Without
                # them (plain non-graph usage) the child supervises
                # unregistered — the same contract its parent ran on.
                subagent_timeout=self._subagent_timeout,
                max_concurrent_subagents=self._max_concurrent_subagents,
                subagent_input_hook=run.request.subagent_input_hook,
                subagent_output_hook=run.request.subagent_output_hook,
                subagent_run_wrapper=run.request.subagent_run_wrapper,
            )
                # The run wrapper executes INSIDE this child task, so
                # its context bindings reach the child's LLM calls and
                # no one else's (sibling spawns each get their own).
                wrapper = None
                if self._subagent_run_wrapper is not None:
                    try:
                        wrapper = self._subagent_run_wrapper(run)
                    except Exception as exc:  # noqa: BLE001 — must not kill the child
                        logger.warning("subagent %s run wrapper failed: %s",
                                       run.run_id, exc)
                if wrapper is not None:
                    with wrapper:
                        result = await child.run(
                            run.request.goal, data=run.request.data,
                            user_addendum=run.request.user_addendum,
                            inherited_messages=run.request.inherit_transcript)
                else:
                    result = await child.run(
                        run.request.goal, data=run.request.data,
                        user_addendum=run.request.user_addendum,
                        inherited_messages=run.request.inherit_transcript)
        except asyncio.CancelledError:
            run.status = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced as tool text
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            return f"subagent {run.run_id} failed: {run.error}"
        else:
            run.status = "completed"
            run.answer = result.answer
            run.steps = result.steps
            run.tool_call_count = len(result.tool_calls)
            return format_subagent_result(run)
        finally:
            if run.ended_at is None:
                run.ended_at = datetime.datetime.now(SHANGHAI_TZ)

    async def _apply_output_hook(self, run: SubagentRun,
                                 text: str) -> str:
        """OUTPUT interception: let the hook rewrite the tool text; on
        hook failure fall back to the raw text (the child's work must
        not be lost to a broken redactor) and record the hook error."""
        if self._subagent_output_hook is None:
            return text
        try:
            return await self._subagent_output_hook(run)
        except Exception as exc:  # noqa: BLE001 — degrade, don't discard
            logger.warning("subagent %s output hook failed: %s",
                           run.run_id, exc)
            run.error = f"output hook: {type(exc).__name__}: {exc}"
            return text

    # ------------------------------------------------------------------
    # Background spawns: detached supervision + collection
    # ------------------------------------------------------------------

    async def _supervise_background(self, run: SubagentRun) -> None:
        """A detached spawn's supervisor: the SAME timeout/output policy
        as an awaited spawn, but the finished text parks in the
        background queue instead of entering the transcript — until
        ``collect_subagents`` gathers it. A cancelled spawn parks nothing
        (the canceller settles its records)."""
        try:
            text = await asyncio.wait_for(
                run.task, timeout=self._subagent_timeout)
        except asyncio.TimeoutError:
            # wait_for cancelled the child; its own handler marked it.
            run.status = "timed_out"
            text = (f"subagent {run.run_id} timed out after "
                    f"{self._subagent_timeout:g}s and was stopped; "
                    "narrow the delegated goal or split it")
        except asyncio.CancelledError:
            run.status = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — belt under the child's own guard
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            text = f"subagent {run.run_id} failed: {run.error}"
        text = await self._apply_output_hook(run, text)
        self._background_results[run.run_id] = text

    async def _collect_subagents(self, arguments: Dict[str, Any]) -> str:
        """The collect_subagents tool: gather finished spawn results (and
        report still-running ones). ``wait`` blocks on the outstanding
        tasks up to ``timeout`` seconds — the model's gather point for
        spawns it spawned earlier."""
        wanted = {str(r).strip() for r in (arguments.get("run_ids") or [])
                  if str(r).strip()} or None
        runs = [run for run in self._uncollected_spawns()
                if wanted is None or run.run_id in wanted]
        if not runs:
            return ("no outstanding background subagents"
                    + (f" matching {sorted(wanted)}" if wanted else ""))
        if arguments.get("wait") is not False:
            timeout = arguments.get("timeout")
            pending = [run.task for run in runs
                       if run.task is not None and not run.task.done()]
            if pending:
                try:
                    seconds = (float(timeout) if timeout else 120.0)
                except (TypeError, ValueError):
                    seconds = 120.0
                await asyncio.wait(pending, timeout=max(1.0, seconds))
        lines: List[str] = []
        for run in runs:
            text = self._background_results.get(run.run_id)
            done = run.task is None or run.task.done()
            if text is not None:
                self._background_collected.add(run.run_id)
                lines.append(f"subagent {run.run_id} [{run.status}]:\n"
                             f"{text}")
            elif done and run.status in ("cancelled", "timed_out", "failed"):
                # Terminal with nothing to gather — a cancelled or
                # timed-out spawn. Counted as gathered: there is nothing
                # left to wait for, and the answer gate must open.
                self._background_collected.add(run.run_id)
                lines.append(f"subagent {run.run_id} [{run.status}] "
                             "finished without a result (cancelled or "
                             "timed out)")
            elif done:
                # Completed a heartbeat ago — its supervisor is parking
                # the result right now. NOT gathered yet; the model
                # collects again (and the answer gate holds until it
                # does, so the answer never drops a finished spawn).
                lines.append(f"subagent {run.run_id} just finished — "
                             "collect again to read its result")
            else:
                lines.append(f"subagent {run.run_id} [{run.status}] still "
                             "running — collect again later")
        return "\n\n".join(lines)
