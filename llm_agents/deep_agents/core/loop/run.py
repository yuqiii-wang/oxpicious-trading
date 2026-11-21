"""llm_agents.deep_agents.core.loop.run — the deep-agent RUN loop.

The mixin carrying ``DeepAgent.run`` — the Claude Code school of ONE
loop, no framework: ONE list of messages, ONE provider call per step,
results appended as ``tool`` messages, loop until the model replies
without a tool call (or ``max_steps`` runs out). Split out of the
former flat ``core/loop.py`` (2026-11-15); the loop orchestrates the
composed concerns through ``self`` — the router lanes, the tool
executor, the sandbox breaker, the answer/output gates below.

Two gates stand before a reply becomes the final answer, both bounded
and both shipping the answer when their rounds run out (the
always-answer contract): the ANSWER GATE (uncollected background spawns
are nudged for collection first) and the OUTPUT-VALIDATION gate
(``core.output_validation``): structurally broken replies — empty,
truncated mid-fence, ending on the truncation marker or a dangling
heading, degenerately repetitive, or self-declared JSON that does not
parse — get ONE repair round with the specific complaints fed back as a
user message. Every request the loop sends carries the provider's
PER-REQUEST token ceiling (``max_tokens`` — ``core.provider``), the
loose runaway guard that lets the runner's step limits stay generous.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.core.delegation import (
    COLLECT_SUBAGENTS_SCHEMA,
    FORK_TOOL_SCHEMA,
    SUBAGENT_TOOL_SCHEMA,
)
from llm_agents.deep_agents.core.loop.result import AgentResult
from llm_agents.deep_agents.core.output_validation import (
    repair_user_message,
    validate_output,
)
from llm_agents.deep_agents.core.tools import user_content
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.payload_guard import guard_text

# The caution trail (monitor.caution_log): AI-found suspicions —
# validation complaints that survive their repair round — land there.
from _common.caution_log import record_ai_suspicion

logger = logging.getLogger(__name__)


class _RunLoop:
    """The conversation loop; composed into ``DeepAgent``."""

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
        # The OUTPUT SEAT's reserve once-guard (2026-11-15): distinct
        # from _join_nudged — the output node has no join lane; its
        # reserve only appends the final-document nudge.
        self._output_reserve_nudged = False
        # THE LENGTH-CUT CONTINUE's streak (2026-11-15, run d7b24a36):
        # consecutive no-content output-ceiling cuts — 2 in a row stand
        # down to the ship-anyway answer gate.
        self._length_cut_streak = 0
        self._steps_used = 0
        self._tool_calls_used = 0
        # THE REPEAT GUARD's state (2026-11-13, run 4597f052): the
        # previous executed call's (tool, raw-arguments) signature and
        # whether its result was a failure — an IDENTICAL call right
        # after a failure can only repeat it (2893's edit x2, 2905's
        # run x2 with nothing changed in between).
        self._prev_call_sig: Optional[tuple] = None
        self._prev_call_failed = False

        try:
            for step in range(1, self._max_steps + 1):
                self._steps_used = step
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
                    # THE LENGTH-CUT CONTINUE (2026-11-15, run d7b24a36):
                    # a reply the per-request output ceiling cut with the
                    # tool-call JSON never closing arrives as content=''
                    # AND calls=[] — the path below read that as a FINAL
                    # empty answer, spent the ONE output-repair round, and
                    # shipped it as a COMPLETED empty finding (the run's
                    # risk_analysis / pessimistic_review seats: two
                    # 8192-token cuts each, zero note rows — and the
                    # result graph's output node then burned its whole
                    # step budget hunting reviews that never landed). A
                    # cut is not a finish: continue with the resend
                    # remedy (the same teach the cut-arguments lane
                    # gives). TWO consecutive cut-empties stand down to
                    # the old ship-anyway behavior — a pathological cut
                    # loop must not burn the whole budget; the last step
                    # ships as before too.
                    if (message.get("finish_reason") == "length"
                            and not answer):
                        self._length_cut_streak += 1
                        if (self._length_cut_streak <= 2
                                and step < self._max_steps):
                            logger.info(
                                "step %d/%d: reply cut by the output "
                                "ceiling with no content — continue with "
                                "the resend remedy", step, self._max_steps)
                            messages.append({
                                "role": "assistant",
                                "content": "",
                            })
                            messages.append({
                                "role": "user",
                                "content": (
                                    "Your previous reply hit the max "
                                    "output tokens mid-generation and "
                                    "arrived empty (the tool-call JSON "
                                    "never closed). Resend the SAME work "
                                    "with SHORTER arguments: split a "
                                    "large file write into blocks, or "
                                    "patch one block with "
                                    "sandbox_edit_file instead of "
                                    "resending the whole content — or "
                                    "answer directly in plain text."
                                ),
                            })
                            continue
                    else:
                        self._length_cut_streak = 0
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
                        # The complaints survive the repair round and the
                        # answer SHIPS — that is an AI-found suspicion
                        # (monitor.caution_log, the Cautions tab): the
                        # log line above dies with the process, the row
                        # stays queryable.
                        record_ai_suspicion(
                            f"agent reply shipped past output validation: "
                            f"{issues[0]}",
                            dedupe_key="output-validation",
                            detail={"issues": issues, "steps": step,
                                    "answer_chars": len(answer or "")},
                            service="llm_agents.deep_agents",
                        )
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
                    self._tool_calls_used = len(tool_calls_log)
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
                elif (self._node_role == "output"
                      and not self._output_reserve_nudged
                      and step == self._max_steps - 1):
                    # THE OUTPUT SEAT'S RESERVE (2026-11-15, run
                    # d7b24a36): the result graph's output node died at
                    # the cap mid-retrieval with NO final document —
                    # one step before the cap it is told to ship what
                    # it has. The answer gate ships the text reply; a
                    # still-calling last reply ends at the cap exactly
                    # as before (no join lane here).
                    self._output_reserve_nudged = True
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Final-document reserve] ONE step remains. "
                            "Do not start new tool work: write your "
                            "FINAL document NOW in one message from what "
                            "you have already gathered — the complete "
                            "answer this node must deliver, naming its "
                            "gaps explicitly where evidence is missing. "
                            "This message becomes the run's final "
                            "result."
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
            f"final answer (spent {self._steps_used} step(s), "
            f"{self._tool_calls_used} tool call(s)); narrow the goal "
            "or raise --max-steps")
