"""llm_agents.deep_agents.core.loop.spawner — the SUBAGENT SPAWNER
(split out of the former flat ``core/loop.py``, 2026-11-15).

The delegation supervision, still LOOP METHODS on the composed
``DeepAgent`` (the supervision state lives in the loop — the blueprint
contract): ``_run_subagent`` supervises one delegation end to end
(input hook → registry → Task → semaphore → timeout → output hook →
the failure-probe lane), ``_execute_subagent`` is the child coroutine
(the child ``DeepAgent`` construction with the whole policy propagated
one level down), and the background lane (``_supervise_background`` +
``_collect_subagents``) parks finished results until the model gathers
them — the answer gate holds until it does. ``run_delegation`` is the
programmatic entry the step graph uses for delegated plan steps.

Nothing here raises — every failure mode is tool-result text the
parent model can react to.
"""
from __future__ import annotations

import asyncio
import copy
import datetime
import logging
import uuid
from typing import (
    TYPE_CHECKING, Any, Dict, List, Optional,
)

from llm_agents._core.models import SHANGHAI_TZ
from llm_agents.deep_agents.core.delegation import (
    DELEGATION_GUIDANCE,
    FORK_TOOL_NAME,
    SUBAGENT_TOOL_NAME,
    SubagentRequest,
    SubagentRun,
    SubagentSpawnRejected,
    format_subagent_result,
)
from llm_agents.deep_agents.limits import run_limits

if TYPE_CHECKING:
    from llm_agents.deep_agents.core.loop import DeepAgent

logger = logging.getLogger(__name__)


class _SubagentSpawner:
    """The spawner: intercept -> register -> supervise -> intercept;
    composed into ``DeepAgent``."""

    def _uncollected_spawns(self) -> List[SubagentRun]:
        """Background spawns whose result the model has not gathered —
        the final-answer gate's set (running OR finished-uncollected)."""
        return [run for run in self._subagent_runs
                if run.background
                and run.run_id not in self._background_collected]

    async def run_delegation(self, goal: str, *,
                             max_steps: Optional[int] = None,
                             study_focus: Optional[str] = None,
                             user_addendum: Optional[str] = None,
                             ) -> str:
        """Run ONE delegated sub-goal through the spawner — no main
        conversation at all. The thread runner's step-graph engine calls
        this for a plan step that carries a delegation goal: the child
        runs under the full supervision policy (hooks → registry →
        semaphore → timeout → output hook) and its result text (or
        failure text — nothing here raises) becomes the step's finding.
        ``study_focus`` — the plan step's EXPLICIT scope-extension
        trigger (core/authorization): the child's spawn hook KG-locates
        the related tables and grants a (chain, node) extension row.
        ``user_addendum`` — an extra TEXT block riding the child's first
        user message (the delegated resume's supervisor instruction,
        run d7b24a36; the input hook composes its hint blocks onto it).
        The agent instance is never ``run()`` in this mode; its registry
        still records the child (``subagent_runs()``)."""
        return await self._run_subagent({
            "goal": goal,
            **({"max_steps": int(max_steps)} if max_steps else {}),
            **({"study_focus": study_focus} if study_focus else {}),
            **({"user_addendum": user_addendum} if user_addendum else {}),
        })

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
        # A caller-side addendum (the delegated resume's supervisor
        # instruction, run d7b24a36) — rides the child's first user
        # message behind the structured fence; the input hook COMPOSES
        # its own blocks onto this, never over it.
        caller_addendum = (str(arguments.get("user_addendum") or "")
                           .strip() or None)
        run = SubagentRun(
            run_id=uuid.uuid4().hex[:8],
            request=SubagentRequest(
                goal=goal, max_steps=max_steps, background=background,
                depth=self._spawn_depth + 1, role=role,
                study_focus=(str(arguments.get("study_focus") or "")
                             .strip() or None),
                user_addendum=caller_addendum,
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
        # The composed class resolves lazily — the mixin modules load
        # before the package's composition statement, and by call time
        # the import is fully resolved (no import cycle).
        from llm_agents.deep_agents.core.loop import DeepAgent
        child: Optional["DeepAgent"] = None
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
                        # THE SCOPE INHERITANCE (core/authorization,
                        # 2026-11-13): an extended child carries its own
                        # stamped context; every other child inherits
                        # this agent's scope verbatim — the context
                        # object IS the inheritance mechanism.
                        authorization=(run.request.authorization
                                       or self._authorization),
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
            self._stamp_child_usage(run, child)
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced as tool text
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            self._stamp_child_usage(run, child)
            return format_subagent_result(run)
        else:
            run.status = "completed"
            run.answer = result.answer
            run.steps = result.steps
            run.tool_call_count = len(result.tool_calls)
            return format_subagent_result(run)
        finally:
            if run.ended_at is None:
                run.ended_at = datetime.datetime.now(SHANGHAI_TZ)

    @staticmethod
    def _stamp_child_usage(run: SubagentRun,
                           child: Optional["DeepAgent"]) -> None:
        """Carry the child's actual usage onto its run record on the
        FAILURE paths — the success branch reads the AgentResult, but a
        child that raised (the step cap, a crash) used to report
        0 steps / 0 tool calls, and the parent's finding text then told
        the supervisor the spawn never worked (run d7b24a36's reviews
        diagnosed a 32-step analysis as an empty-run hiccup)."""
        if child is None:
            return
        try:
            run.steps = int(getattr(child, "_steps_used", 0) or 0)
            run.tool_call_count = int(
                getattr(child, "_tool_calls_used", 0) or 0)
        except Exception:  # noqa: BLE001 — counters never fail the record
            pass

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
