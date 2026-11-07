"""llm_agents.deep_agents.supervisor.poller — the SUPERVISOR SESSION:
the per-run backend polling job (2026-11-06). Created by the runner
just before ``graph.ainvoke``, it lives for the WHOLE run beside the
graph (sharing no node, edge or data lane with it):

* WATCH — ``RepairChannel.wait_failures`` blocks until the step lane
  signals a root-path failure (instant, the pause window is open) or
  the poll interval lapses (the independent belt: read the settled
  node states from the DB).
* CHECK WHAT TO DO NEXT — on a failure the CODE filter runs first
  (:func:`repair.is_transient` + the retry budgets): a non-repairable
  class returns an immediate ``no-action`` verdict with ZERO LLM
  (unhandled — the final sweep still reviews it). Only a transient,
  budgeted candidate wakes the supervisor agent (ONE bounded
  tool-carrying intervention in its own fresh context). When the
  intervention call ITSELF fails transiently (under a rate limit it
  rides the same hot provider), the code issues the retry verdict
  itself — the model-unreachable fallback in
  :meth:`SupervisorSession._handle_lane_failures` — instead of letting
  the failed ask collapse the class to ``no-action`` (2026-11-06).
* RESUME THE NODE — when the agent's ``Action: retry-step`` verdict
  passes :func:`repair.should_retry`, the poller re-opens the failed
  state row (``PLAN_STORE.reopen_state`` — failed → pending) and
  hands the verdicts back; the waiting lane's normal routing re-picks
  the row next hop. The run resumes.
* SWEEP — when the runner closes the channel (graph done), the
  session runs the final sweep (:meth:`SupervisorSession.sweep` — the
  former ``watchdog.run_supervisor_watchdog``) minus the failures it
  already reviewed, closing the run-start marker — the old post-run
  tail, absorbed into the session.

ONE class since 2026-11-06 (the former module functions): the session's
provider / channel / thread ids / goal / model / writer factory are the
object's fields, and every rung — the lane-failure handler, the
intervention, the poll loop (:meth:`SupervisorSession.run`) and the
final sweep (:meth:`SupervisorSession.sweep`) — is its method.

Fail-soft end to end: every rung catches its own errors (a failed
intervention settles verdicts ``no-action`` and changes nothing about
the run); the session never raises into the runner.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from llm_agents._core.request_log import bind_request_chain, end_request_chain
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.store import (
    PLAN_STORE, StateRow,
)
from llm_agents.deep_agents.supervisor.engine import (
    root_path_failures, intervene_on_failure,
)
from llm_agents.deep_agents.supervisor.prompts import (
    fetch_supervisor_prompts,
)
from llm_agents.deep_agents.supervisor.repair import (
    ACTION_IGNORE, ACTION_NONE, ACTION_RETRY, RepairChannel, Verdict,
    is_rate_limited, is_transient, should_retry,
)
from llm_agents.deep_agents.supervisor.tools import SupervisorToolKit

if TYPE_CHECKING:  # no runtime langgraph import — the runner injects
    from langgraph.store.base import BaseStore
    from llm_agents.deep_agents.langgraph.state_store import NodeRunWriter

logger = logging.getLogger("deep_agents.supervisor")

# The final sweep's wall-clock belt (the historical tail timeout).
SWEEP_TIMEOUT_SEC = 180.0

# The runner-side writer factory: (llm: bool) -> a NodeRunWriter for
# THIS run's supervisor namespace (the marker is the llm=False form).
# (Relocated here from watchdog.py — the sweep is a session method.)
WriterFactory = Callable[[bool], "NodeRunWriter"]


class SupervisorSession:
    """The per-run supervisor session (2026-11-06 — the former
    ``supervisor_session`` + ``run_supervisor_watchdog`` module
    functions): the poll loop, the lane-failure handler, the bounded
    intervention and the final sweep as methods of ONE object holding
    the run's binding (provider, channel, thread ids, goal, writer
    factory). THE SUPER BINDING: ``super_thread_id`` (the three-graph
    chain's root id) is the session's OWN id — every keyed artifact
    (review rows, kit scope, reopen/ignore events, the sweep) resolves
    through ``scope_id``, and every state read widens to the run's
    WHOLE CHAIN; None keeps the plain thread read (legacy callers/
    tests). Never raises into the runner."""

    def __init__(
        self,
        provider: ZhipuChatProvider,
        store: Optional["BaseStore"],
        *,
        thread_id: str,
        goal: str,
        channel: RepairChannel,
        marker: Optional["NodeRunWriter"] = None,
        writer_factory: Optional[WriterFactory] = None,
        model: Optional[str] = None,
        intervention_agent=None,
        super_thread_id: Optional[str] = None,
    ) -> None:
        self.provider = provider
        self.store = store
        self.thread_id = thread_id
        self.goal = goal
        self.channel = channel
        self.marker = marker
        self.writer_factory = writer_factory
        self.model = model
        self.intervention_agent = intervention_agent
        self.super_thread_id = super_thread_id
        self.scope_id = super_thread_id or thread_id

    # ------------------------------------------------------------------
    # State reads
    # ------------------------------------------------------------------
    async def _watched_states(self) -> list:
        """The rows the session watches: the run's WHOLE CHAIN when the
        super id is known (every thread of the run), else the one thread
        (legacy callers/tests)."""
        if self.super_thread_id:
            return await PLAN_STORE.list_super_states(self.super_thread_id)
        return await PLAN_STORE.list_thread_states(self.thread_id)

    # ------------------------------------------------------------------
    # The bounded intervention
    # ------------------------------------------------------------------
    async def _intervene(
        self,
        *,
        rows: List[StateRow],
        failures: List[StateRow],
        repair_eligible: bool,
        writer=None,
    ) -> Tuple[str, str]:
        """The ONE bounded intervention (fresh context, own tool kit,
        review recorded): (text, action). *writer* — the SupervisorAgent's
        llm writer for THIS conversation: when given, the calls run inside
        the caller's already-bound chain and the review persists keyed to
        the writer's context id (a first-class context row); None keeps
        the plain fail-soft call (chain_id NULL). Raises on provider
        failure — callers fail-soft."""
        prompts = await fetch_supervisor_prompts()
        kit = SupervisorToolKit(self.scope_id,
                                descriptions=prompts.tool_descriptions)
        outcome = await intervene_on_failure(
            self.provider.chat_complete,
            goal=self.goal, rows=rows, failures=failures, model=self.model,
            system_prompt=prompts.system,
            builtin_tools=kit.builtin_tools(),
            tool_addendum=kit.prompt_addendum(),
            repair_eligible=repair_eligible,
            message_frame=prompts.intervention,
        )
        try:
            await PLAN_STORE.record_supervisor_review(
                self.scope_id, outcome.text,
                llm_context_id=getattr(writer, "context_id", None))
        except Exception as exc:  # noqa: BLE001 — fail-soft
            logger.warning("supervisor review persist failed: %s", exc)
        return outcome.text, outcome.action

    # ------------------------------------------------------------------
    # The pause-window decision
    # ------------------------------------------------------------------
    async def _handle_lane_failures(
        self,
        *,
        pending: List[Tuple[int, str]],
        intervention_writer=None,
    ) -> bool:
        """The PAUSE WINDOW's decision: verdicts for the lane-signalled
        failures (ANY root-path failure — a wave member included; the
        join holds every before-node's settle). ONE bounded agent call
        decides the batch — its ``Action:`` line is advisory until the
        code gates apply it: ``retry-step`` re-opens the failed row (a
        RATE-LIMIT class retry is THROTTLED inside the window before the
        hop re-runs), ``ignore-step`` settles the row skipped wearing the
        supervisor's note and the run resumes PAST it, ``no-action``
        leaves the row failed. A retry verdict CARRIES the supervisor's
        INSTRUCTION (the bounded reply text) — the resumed node agent's
        resume context. Failures already asked-and-answered (the
        ``asked`` set — e.g. a retry budget spent) fast-path no-action
        with zero LLM. *intervention_writer* (the SupervisorAgent's llm
        writer, chain bound by the caller) makes the intervention a
        first-class context row; None keeps the plain fail-soft call.

        THE MODEL-UNREACHABLE FALLBACK (2026-11-06): when the ask itself
        fails TRANSIENTLY — the verdict call rides the same provider as
        the failed step, so a rate limit kills it too, immediately (zhipu
        1302 is not in the transport's retry codes) — the CODE issues the
        verdict instead of collapsing to no-action: every transient-class
        step failure gets its budget-gated retry-step (rate-limited ones
        throttled; ``should_retry`` still gates on the STEP's error, so a
        non-transient step failure is never smuggled through by a
        transient ask failure). Returns whether a supervisor LLM call was
        actually spent."""
        channel = self.channel
        limits = run_limits()
        # Ask-once bookkeeping: a sid the agent was already asked about
        # (and whose retry budget is spent) never spends another call.
        askable = [(sid, err) for sid, err in pending
                   if sid not in channel.asked
                   or channel.attempts.get(sid, 0)
                   < limits.supervisor_max_node_retries]
        if not askable:
            channel.set_verdicts(
                {sid: Verdict(action=ACTION_NONE) for sid, _ in pending})
            return False
        try:
            # The CHAIN read, never the one thread: a lane-signalled
            # failure is usually a CUSTOM/RESULT segment row — reading the
            # root langgraph thread alone woke the agent with an EMPTY
            # failure manifest over a plan-only tree (the live fallback
            # probe's supervisor reviews said exactly that). The super
            # binding widens the read to every thread of the run.
            rows = await self._watched_states()
        except Exception as exc:  # noqa: BLE001 — cannot even frame the call
            logger.warning("supervisor poller: state read failed: %s", exc)
            channel.set_verdicts(
                {sid: Verdict(action=ACTION_NONE) for sid, _ in pending})
            return False
        failed = [s for s in root_path_failures(rows)
                  if s.state_id in {sid for sid, _ in pending}]
        verdicts: Dict[int, Verdict] = {}
        agent_spent = False
        throttled = False
        # The verdict call's own failure class (set only when it failed
        # transiently — the code-fallback path below).
        verdict_exc_rate_limited = False
        try:
            review, action = await self._intervene(
                rows=rows, failures=failed or [], repair_eligible=True,
                writer=intervention_writer)
            instruction = " ".join((review or "").split())[:600]
            agent_spent = True
        except Exception as exc:  # noqa: BLE001 — fail-soft, BUT the CODE
            # classifies the ask's own failure before giving up: the
            # verdict call rides the SAME provider as the failed step, so
            # under a rate limit it cannot even be asked — its own call
            # fails fast (zhipu code 1302 is NOT in the transport's retry
            # codes) and this except IS the rate-limit event. Letting the
            # ask failure flip a transient class to no-action is exactly
            # what left rate-limited nodes failed instead of throttled and
            # resumed (the throttle was dead code in the scenario it was
            # built for). When the model is unreachable the code issues
            # the verdict itself — the structural policy the model is
            # taught: a transient class retries (budget-gated in the loop
            # below via should_retry; should_retry still reads the STEP's
            # error, so a non-transient step failure can never be smuggled
            # through by a transient ask failure), a rate-limit one
            # throttled. Anything else keeps no-action.
            exc_text = f"{type(exc).__name__}: {exc}"
            if not is_transient(exc_text):
                logger.warning("supervisor intervention failed: %s", exc)
                channel.set_verdicts(
                    {sid: Verdict(action=ACTION_NONE) for sid, _ in pending})
                return False
            logger.warning(
                "supervisor intervention failed transiently (%s) — the "
                "code fallback retries the transient class", exc_text)
            action = ACTION_RETRY
            instruction = (
                "[Auto-retry — decided in code] The supervisor's verdict "
                f"call itself failed transiently ({exc_text[:200]}), so "
                "the retry was decided structurally: the error class is "
                "transient and re-runnable. Resume the step from where it "
                "failed; do not restart the whole plan.")
            verdict_exc_rate_limited = is_rate_limited(exc_text)
        retried_any = False
        for sid, err in pending:
            action_sid = action if (sid, err) in askable else ACTION_NONE
            if action_sid == ACTION_RETRY:
                if not should_retry(channel, sid, err, action):
                    action_sid = ACTION_NONE
                else:
                    try:
                        if await PLAN_STORE.reopen_state(
                                sid, thread_id=self.scope_id):
                            channel.attempts[sid] = (
                                channel.attempts.get(sid, 0) + 1)
                            channel.repairs_used += 1
                            retried_any = True
                            if is_rate_limited(err):
                                throttled = True
                        else:
                            action_sid = ACTION_NONE
                    except Exception as exc:  # noqa: BLE001 — fail-soft
                        logger.warning(
                            "supervisor reopen failed (%s): %s", sid, exc)
                        action_sid = ACTION_NONE
            elif action_sid == ACTION_IGNORE:
                try:
                    if not await PLAN_STORE.ignore_state(
                            sid, thread_id=self.scope_id):
                        action_sid = ACTION_NONE  # row already moved on
                except Exception as exc:  # noqa: BLE001 — fail-soft
                    logger.warning(
                        "supervisor ignore failed (%s): %s", sid, exc)
                    action_sid = ACTION_NONE
            verdicts[sid] = Verdict(
                action=action_sid,
                instruction=instruction
                if action_sid == ACTION_RETRY else "")
            channel.asked.add(sid)
            # The agent reviewed every pending failure this call — the
            # sweep skips them (a retried row reopens; an ignored row
            # settles skipped; a no-action row stays failed and already
            # carries its review).
            channel.handled.add(sid)
        if ((throttled or verdict_exc_rate_limited)
                and retried_any
                and limits.supervisor_retry_throttle_sec > 0):
            # The lane is paused in its window — the cooldown rides it:
            # hold the re-run back so the hot account cools first. Either
            # the STEP's error was rate-limited (the model said retry) or
            # the verdict call itself just got rate-limited (the provider
            # is hot right now — the code fallback decided the retry); a
            # retry must actually have been APPLIED (a budget-refused or
            # re-open-refused batch re-runs nothing, so nothing to hold).
            logger.info(
                "supervisor repair: throttling rate-limit retry %ss",
                limits.supervisor_retry_throttle_sec)
            await asyncio.sleep(limits.supervisor_retry_throttle_sec)
        channel.set_verdicts(verdicts)
        return agent_spent  # was a supervisor LLM call actually spent?

    async def _intervention_under_agent(
        self, *, pending: List[Tuple[int, str]],
    ) -> None:
        """The lane-failure decision run as the SUPERVISOR AGENT's own
        conversation: its llm writer begun, chain bound, the handler
        inside, the node item closed — a first-class context row (the
        pre-agent poller ran these with chain_id NULL). *intervention_agent*
        is None-able for the plain fail-soft path (tests)."""
        if self.intervention_agent is None:
            await self._handle_lane_failures(pending=pending)
            return
        # The agent's llm-writer hook (the supervisor's ctor factory
        # defaults llm=False for the marker — interventions need contexts).
        if hasattr(self.intervention_agent, "new_llm_writer"):
            new_writer = self.intervention_agent.new_llm_writer
        else:
            new_writer = self.intervention_agent.new_writer
        writer = new_writer()
        await self.intervention_agent.begin_node(writer)
        agent_spent = False
        try:
            with self.intervention_agent.chain(writer):
                agent_spent = await self._handle_lane_failures(
                    pending=pending, intervention_writer=writer)
        except Exception as exc:  # noqa: BLE001 — the handler self-guards
            logger.warning("supervisor intervention context failed: %s", exc)
            await self.intervention_agent.close_node(
                writer, "failed", error=f"{type(exc).__name__}: {exc}",
                n_llm_calls=0)
            return
        await self.intervention_agent.close_node(
            writer, "completed", n_llm_calls=1 if agent_spent else 0)

    # ------------------------------------------------------------------
    # The poll loop
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """The per-run poll loop — poll → decide → resume/ignore, until
        the channel closes (the runner owns the task). The runner owns
        the task; closing the channel ends the loop and :meth:`sweep`
        closes the marker. Never raises (CancelledError returns)."""
        limits = run_limits()
        try:
            while not self.channel.closed:
                try:
                    lane_signalled = await self.channel.wait_failures(
                        interval_sec=limits.supervisor_poll_interval_sec)
                except asyncio.CancelledError:
                    raise
                if self.channel.closed:
                    break
                if lane_signalled:
                    pending = self.channel.take_pending()
                    if pending:
                        await self._intervention_under_agent(
                            pending=pending)
                    continue
                # The interval belt: read the settled states; a root-path
                # failure the lane never signalled (plan row, a review
                # member) gets its observe review early — ONE attempt per
                # failure (the attempted set — a failed review under a
                # throttled provider must not retry-storm every interval;
                # the final sweep still reviews anything left unreviewed).
                # Failures the lane has signalled (pending_ids) are
                # excluded — their verdict belongs to the pause-window
                # path.
                with contextlib.suppress(Exception):
                    rows = await self._watched_states()
                    fresh = [s for s in root_path_failures(rows)
                             if s.state_id not in self.channel.handled
                             and s.state_id not in self.channel.attempted
                             and s.state_id not in self.channel.pending_ids]
                    if fresh:
                        self.channel.attempted.update(
                            s.state_id for s in fresh)
                        try:
                            await self._intervene(
                                rows=rows, failures=fresh,
                                repair_eligible=False)
                            self.channel.handled.update(
                                s.state_id for s in fresh)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "supervisor early review failed: %s", exc)
        except asyncio.CancelledError:  # runner's finally — run is over
            return
        # The FINAL SWEEP — the absorbed post-run tail: idle-close the
        # marker on a healthy tree, else the observe review, deduped
        # against everything handled mid-run.
        with contextlib.suppress(Exception):
            await self.sweep()

    # ------------------------------------------------------------------
    # The final sweep (the former watchdog tail)
    # ------------------------------------------------------------------
    async def sweep(
        self, *, skip_state_ids: "frozenset[int]" = frozenset(),
    ) -> None:
        """The watchdog's tail sweep — the supervisor's post-graph
        observe pass, keyed through the SUPER id (its chain read already
        rides super_thread_id). IDLE on healthy runs: the trigger is
        :func:`engine.root_path_failures` — a pure filter over the
        settled node states — and an empty result just closes the
        watching marker completed with zero LLM calls (no context minted,
        no registry item, no review row, and zero prompt fetches — the
        skills self-fetch only runs on the waking path). Only a
        ROOT-PATH failure wakes the model: a bounded intervention in the
        session's OWN fresh context (request chain bound, review
        persisted as the kind supervisor-review row keyed to that
        context) — OBSERVE MODE: it diagnoses the error and recommends
        whether the plan/graph topology needs an update, never applies
        one. Never raises (every rung fail-soft — the run's recorded
        result is already final). *skip_state_ids* — the failures the
        mid-run session already reviewed (one review per failure, not
        one per lane)."""
        if self.marker is None:
            if self.writer_factory is None:
                return
            self.marker = self.writer_factory(False)
            await self.marker.begin(None)
        marker = self.marker
        try:
            rows = await self._watched_states()
        except Exception as exc:  # noqa: BLE001 — idle-close the marker
            logger.warning("supervisor watchdog: state sweep failed: %s", exc)
            with contextlib.suppress(Exception):
                await marker.finish("failed",
                                    error=f"{type(exc).__name__}: {exc}")
            return
        failures = [s for s in root_path_failures(rows)
                    if s.state_id not in skip_state_ids]
        if not failures:
            # IDLE — the healthy common case: zero LLM, zero context.
            with contextlib.suppress(Exception):
                await marker.finish("completed")
            return
        logger.info("supervisor watchdog: %d root-path failure(s) — "
                    "intervening", len(failures))
        # The waking fetches: the supervisor's own skills (its system row +
        # its kit's type-4 description rows), fail-soft to the built-ins.
        prompts = await fetch_supervisor_prompts()
        kit = SupervisorToolKit(self.scope_id,
                                descriptions=prompts.tool_descriptions)
        writer = self.writer_factory(True)
        await writer.begin(None)
        review: Optional[str] = None
        error: Optional[str] = None
        try:
            with bind_request_chain(writer.context_id):
                outcome = await intervene_on_failure(
                    self.provider.chat_complete,
                    goal=self.goal, rows=rows, failures=failures,
                    model=self.model,
                    system_prompt=prompts.system,
                    builtin_tools=kit.builtin_tools(),
                    tool_addendum=kit.prompt_addendum(),
                    message_frame=prompts.intervention,
                )
                review = outcome.text
        except Exception as exc:  # noqa: BLE001 — observe mode never vetoes
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("supervisor intervention failed: %s", exc)
        finally:
            end_request_chain(writer.context_id)
        with contextlib.suppress(Exception):
            await writer.finish(
                "failed" if error is not None else "completed",
                n_llm_calls=0 if error is not None else 1,
                error=error, answer_chars=len(review or ""))
        if review is not None:
            try:
                await PLAN_STORE.record_supervisor_review(
                    self.scope_id, review, llm_context_id=writer.context_id)
            except Exception as exc:  # noqa: BLE001 — fail-soft
                logger.warning("supervisor review persist failed: %s", exc)
