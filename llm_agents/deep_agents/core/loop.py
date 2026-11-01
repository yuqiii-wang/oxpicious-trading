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
each child's fork-scoped plan tools there), and the child ctor merges
them beside its inherited sandbox tools.

The spawner (``spawn_subagent``, sync and background-fork forms, plus
``collect_subagents`` and the ``run_delegation`` programmatic entry the
step graph uses for delegated plan steps) is a method-level part of
this loop: delegation records, schemas and hooks live in
``core.delegation``, the policy (timeout, fan-out cap, answer-gating on
uncollected forks, no-orphan-task cleanup) is enforced HERE. Children
carry the spawner themselves below ``MAX_SPAWN_DEPTH`` — a subagent may
fork sub-sub agents, and the whole supervision policy (timeout,
concurrency, hooks) propagates one level down with them.

A ``sandbox`` (``core.sandbox``) rides along when passed on the ctor:
its ``sandbox_*`` tools merge into ``builtin_tools`` (caller-supplied
names win), the sandbox usage contract is appended to the system
prompt, and spawned children INHERIT it — one shared scratch directory
per run, parent and subagents alike.

Two gates stand before a reply becomes the final answer, both bounded
and both shipping the answer when their rounds run out (the
always-answer contract): the ANSWER GATE (uncollected background forks
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
import datetime
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING, Any, Dict, List, Optional, Tuple,
)

from llm_agents._core.models import SHANGHAI_TZ
from llm_agents.deep_agents.core.delegation import (
    COLLECT_SUBAGENTS_SCHEMA,
    COLLECT_TOOL_NAME,
    DELEGATION_GUIDANCE,
    MAX_SPAWN_DEPTH,
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

if TYPE_CHECKING:
    from llm_agents.deep_agents.core.sandbox import Sandbox

logger = logging.getLogger(__name__)

# The root's default prompt: the focused base plus the delegation
# guidance shared by EVERY agent that carries the spawn tool (root step
# conversations and, below MAX_SPAWN_DEPTH, spawned children — a child
# at the cap gets the focused base alone, no spawn mention).
_BASE_SYSTEM_PROMPT = SUBAGENT_SYSTEM_PROMPT
DEFAULT_SYSTEM_PROMPT = _BASE_SYSTEM_PROMPT + DELEGATION_GUIDANCE

# The final answer's structural-validation repair rounds (see
# core.output_validation): one bounded correction turn per conversation —
# the answer-gate nudge's sibling, and the loop-level twin of the
# planner session's propose → validate → correct. An answer still
# invalid after the round ships anyway (format never fails a run).
MAX_OUTPUT_REPAIRS = 1


@dataclass
class AgentResult:
    """The final answer plus how the agent got there."""

    answer: str
    steps: int
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    subagent_runs: List[SubagentRun] = field(default_factory=list)



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
        subagent_timeout: Optional[float] = None,
        max_concurrent_subagents: int = 4,
        subagent_input_hook: Optional[SubagentInputHook] = None,
        subagent_output_hook: Optional[SubagentOutputHook] = None,
        subagent_run_wrapper: Optional[SubagentRunWrapper] = None,
    ) -> None:
        self._provider = provider
        self._mcp = mcp
        self._model = model
        self._max_steps = max(1, max_steps)
        self._system = system if system is not None else DEFAULT_SYSTEM_PROMPT
        self._temperature = temperature
        self._allow_subagents = allow_subagents
        # This agent's rung on the spawn chain (0 = a root step
        # conversation). The spawn/collect tools ride only below
        # MAX_SPAWN_DEPTH — the cap gates recursion structurally.
        self._spawn_depth = max(0, int(spawn_depth))
        self._sandbox = sandbox
        self._builtin_tools = dict(builtin_tools) if builtin_tools else {}
        if sandbox is not None:
            # The sandbox_* tools fill the unset slots; a caller-supplied
            # builtin keeps its name.
            for name, tool in sandbox.builtin_tools().items():
                self._builtin_tools.setdefault(name, tool)
            # The tools exist → the prompt must teach them; appended to
            # the caller's system text too (the runner renders its
            # templates into that text before this ctor runs).
            from llm_agents.deep_agents.core.sandbox import SANDBOX_PROMPT_ADDENDUM
            self._system = f"{self._system}\n\n{SANDBOX_PROMPT_ADDENDUM}"
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
        self._subagent_runs: List[SubagentRun] = []
        # Background-fork bookkeeping: finished results by run id (post
        # output-hook) and the ids the model has gathered — the final
        # answer gate reads them (an uncollected fork blocks finishing).
        self._background_results: Dict[str, str] = {}
        self._background_collected: set = set()
        # Output-validation repair rounds spent (see MAX_OUTPUT_REPAIRS;
        # reset per run()).
        self._output_repairs = 0

    @property
    def _can_spawn(self) -> bool:
        """Whether this agent carries the spawn/collect tools: the
        master switch on AND below the depth cap."""
        return self._allow_subagents and self._spawn_depth < MAX_SPAWN_DEPTH

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
    # The loop
    # ------------------------------------------------------------------

    async def run(self, goal: str,
                  data: Optional[Dict[str, Any]] = None) -> AgentResult:
        """Pursue *goal* until the model answers without a tool call.

        *data* — an arbitrary JSON object attached as the run's structured
        context (see ``user_content``); the agent reads it like any other
        user-message content.
        """
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._system},
            {"role": "user", "content": user_content(goal, data)},
        ]
        tools = await self._mcp.tool_schemas()
        tools = tools + [bt.schema for bt in self._builtin_tools.values()]
        if self._can_spawn:
            tools = tools + [SUBAGENT_TOOL_SCHEMA, COLLECT_SUBAGENTS_SCHEMA]
        tool_calls_log: List[Dict[str, Any]] = []
        runs_before = len(self._subagent_runs)
        self._output_repairs = 0

        try:
            for step in range(1, self._max_steps + 1):
                message = await self._provider.chat_complete(
                    messages, model=self._model, tools=tools,
                    temperature=self._temperature)
                calls = message.get("tool_calls") or []
                if not calls:
                    outstanding = self._uncollected_forks()
                    if outstanding:
                        # The answer gate: a fork the model spawned but
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
                    if issues and self._output_repairs < MAX_OUTPUT_REPAIRS \
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

                messages.append({
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": calls,
                })
                # Sibling calls of one step run concurrently; gather keeps
                # the results in call order and collects failures per call
                # so one bad tool never kills the step.
                outcomes = await asyncio.gather(
                    *(self._execute_call(call, step) for call in calls),
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
                    tool_calls_log.append(
                        {"step": step, "name": name, "output": output})
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id") or "",
                        "content": output,
                    })
        finally:
            # No orphan forks: whichever way the loop ends (answer, step
            # limit, cancellation, crash) still-running background tasks
            # are stopped here — collected ones are done by definition.
            for run in self._subagent_runs:
                if (run.background and run.task is not None
                        and not run.task.done()):
                    run.task.cancel()

        raise RuntimeError(
            f"deep agent hit the {self._max_steps}-step limit without a "
            "final answer; narrow the goal or raise --max-steps")

    def _uncollected_forks(self) -> List[SubagentRun]:
        """Background forks whose result the model has not gathered —
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
                            step: int) -> Tuple[str, str]:
        """Run one tool call; return (tool name, result text for the LLM)."""
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be a JSON object")
        except ValueError as exc:
            return name, f"MCP tool error: bad arguments for {name}: {exc}"
        if name == SUBAGENT_TOOL_NAME:
            logger.info("step %d/%d: spawn subagent(%s)", step,
                        self._max_steps,
                        json.dumps(arguments, ensure_ascii=False))
            return name, await self._run_subagent(arguments)
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

    async def _run_subagent(self, arguments: Dict[str, Any]) -> str:
        """Supervise one delegation end to end; return its tool text.

        Policy is parent-side only: the hooks shape the request and the
        result, the semaphore bounds fan-out, the timeout bounds the
        child's whole life. Nothing here raises — every failure mode is
        tool-result text the parent model can react to.
        """
        goal = str(arguments.get("goal") or "").strip()
        if not goal:
            return (f"{SUBAGENT_TOOL_NAME} error: 'goal' is required and "
                    "must be a non-empty string")
        requested = arguments.get("max_steps")
        max_steps = self._max_steps
        if isinstance(requested, int) and requested >= 1:
            max_steps = min(requested, self._max_steps)
        background = arguments.get("background") is True
        run = SubagentRun(
            run_id=uuid.uuid4().hex[:8],
            request=SubagentRequest(
                goal=goal, max_steps=max_steps, background=background,
                depth=self._spawn_depth + 1),
            background=background,
        )

        # INPUT interception: rewrite or veto before anything starts.
        if self._subagent_input_hook is not None:
            try:
                await self._subagent_input_hook(run)
            except SubagentSpawnRejected as exc:
                return f"{SUBAGENT_TOOL_NAME} rejected by policy: {exc}"
            except Exception as exc:  # noqa: BLE001 — surfaced as tool text
                return (f"subagent input-hook failed: "
                        f"{type(exc).__name__}: {exc}")
        if not run.request.goal.strip():
            return (f"{SUBAGENT_TOOL_NAME} rejected by policy: the input "
                    "hook blanked the goal")
        goal = run.request.goal.strip()
        self._subagent_runs.append(run)

        # The child runs as a real Task so cancel_subagent() and the
        # timeout can control its lifecycle from outside the await.
        run.task = asyncio.ensure_future(self._execute_subagent(run))
        if run.background:
            # FORK: a detached supervisor applies the same timeout/output
            # interception and parks the finished result for collection —
            # the loop keeps moving, the answer gate holds until the
            # model gathers it.
            asyncio.ensure_future(self._supervise_background(run))
            return (f"subagent {run.run_id} forked in the background "
                    f"(goal: {goal[:80]}) — keep working; call "
                    "collect_subagents (wait=true) when you need its "
                    "result, and keep the run id so a plan update can "
                    "stop the fork if the plan changes")
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
                # may fork sub-sub agents when its investigation needs
                # it) and its prompt teaches it; at the cap the child
                # works its goal alone with the focused prompt.
                child = DeepAgent(
                    self._provider, self._mcp,
                    model=self._model, max_steps=run.request.max_steps,
                    system=(run.request.system + DELEGATION_GUIDANCE
                            if (self._allow_subagents
                                and run.request.depth < MAX_SPAWN_DEPTH)
                            else run.request.system),
                    temperature=self._temperature,
                    allow_subagents=self._allow_subagents,
                    spawn_depth=run.request.depth,
                    # The fork's OWN tools ride here — the runner's input
                    # hook mounted its fork-scoped plan kit (update_plan /
                    # update_plan_step) beside the inherited sandbox.
                    builtin_tools=run.request.builtin_tools,
                # Scratch space is shared with the parent: files
                # the parent wrote are readable here and vice versa.
                sandbox=self._sandbox,
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
                        result = await child.run(run.request.goal,
                                                 data=run.request.data)
                else:
                    result = await child.run(run.request.goal,
                                             data=run.request.data)
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
    # Background forks: detached supervision + collection
    # ------------------------------------------------------------------

    async def _supervise_background(self, run: SubagentRun) -> None:
        """A detached fork's supervisor: the SAME timeout/output policy
        as an awaited spawn, but the finished text parks in the
        background queue instead of entering the transcript — until
        ``collect_subagents`` gathers it. A cancelled fork parks nothing
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
        """The collect_subagents tool: gather finished fork results (and
        report still-running ones). ``wait`` blocks on the outstanding
        tasks up to ``timeout`` seconds — the model's gather point for
        forks it spawned earlier."""
        wanted = {str(r).strip() for r in (arguments.get("run_ids") or [])
                  if str(r).strip()} or None
        runs = [run for run in self._uncollected_forks()
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
                # timed-out fork. Counted as gathered: there is nothing
                # left to wait for, and the answer gate must open.
                self._background_collected.add(run.run_id)
                lines.append(f"subagent {run.run_id} [{run.status}] "
                             "finished without a result (cancelled or "
                             "timed out)")
            elif done:
                # Completed a heartbeat ago — its supervisor is parking
                # the result right now. NOT gathered yet; the model
                # collects again (and the answer gate holds until it
                # does, so the answer never drops a finished fork).
                lines.append(f"subagent {run.run_id} just finished — "
                             "collect again to read its result")
            else:
                lines.append(f"subagent {run.run_id} [{run.status}] still "
                             "running — collect again later")
        return "\n\n".join(lines)
