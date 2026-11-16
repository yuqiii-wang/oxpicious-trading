"""llm_agents.deep_agents.supervisor.workers — the supervisor's WORKER
SUB-AGENTS (2026-11-11): one classified event → one worker, launched
by the picker from ``WORKER_FACTORIES``. The former fused
``SupervisorSession`` methods became classes here:

* :class:`RepairWorker` — the pause-window decision (the former
  ``_handle_lane_failures``): askable filter → chain read → ONE bounded
  agent call → the model-unreachable code fallback → the
  ``should_retry`` gate → reopen/ignore writes → the rate-limit
  throttle → the window resolution. Its verdicts wake the waiting lane
  through ``RepairChannel.resolve_window``.
* :class:`ObserveWorker` — the belt's observe review (the former
  inline belt block): ONE attempt per failure (the attempted set — no
  retry-storm under a throttled provider), zero veto, review persisted.
* :class:`SweepWorker` — the final sweep (the former
  ``SupervisorSession.sweep``): idle-close the marker on a healthy
  tree, else the observe-mode review in its own fresh context.

Every worker shares the session binding (provider, channel, goal,
ledger) through :class:`SupervisorWorker` and the ONE bounded
intervention maker (``_intervene`` — the former session method). A NEW
supervision capability = a new ``EventClass`` + one subclass here + one
``WORKER_FACTORIES`` entry; the picker's loop never changes.

Fail-soft end to end: every handle catches its own errors (a failed
intervention settles the window no-action and changes nothing about
the run); the workers never raise into the picker.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, Field

from llm_agents._core.request_log import bind_request_chain, end_request_chain
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.store import PLAN_STORE, StateRow
from llm_agents.deep_agents.supervisor.classify import EventClass
from llm_agents.deep_agents.supervisor.engine import (
    root_path_failures, intervene_on_failure,
)
from llm_agents.deep_agents.supervisor.event_queue import SupervisorEvent
from llm_agents.deep_agents.supervisor.prompts import (
    fetch_supervisor_prompts,
)
from llm_agents.deep_agents.supervisor.repair import (
    ACTION_IGNORE, ACTION_NONE, ACTION_RETRY, Verdict,
    is_rate_limited, is_step_cap, is_transient, should_retry,
)
from llm_agents.deep_agents.supervisor.tools import SupervisorToolKit

if TYPE_CHECKING:  # no runtime langgraph import — the runner injects
    from llm_agents.deep_agents.langgraph.state_store import NodeRunWriter

logger = logging.getLogger("deep_agents.supervisor.workers")


class WorkerOutcome(BaseModel):
    """What one worker dispatch did — the agent-context wrapper reads
    ``agent_spent`` to close the node item's llm count honestly."""

    handled: List[int] = Field(default_factory=list)
    agent_spent: bool = False
    note: str = ""


class SupervisorWorker:
    """Base class: the session binding + the ONE bounded intervention
    maker. A worker is STATELESS between dispatches — every mutable
    supervision fact lives in the channel's ledger (attempts / handled
    / attempted / asked / repairs_used), so any worker can run on any
    event in any order."""

    def __init__(self, session) -> None:
        self.session = session

    async def handle(
        self, event: SupervisorEvent, *,
        writer: Optional["NodeRunWriter"] = None,
    ) -> WorkerOutcome:
        """Handle one classified event (never raises)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # The bounded intervention (the former SupervisorSession._intervene)
    # ------------------------------------------------------------------
    async def _intervene(
        self,
        *,
        rows: List[StateRow],
        failures: List[StateRow],
        repair_eligible: bool,
        writer: Optional["NodeRunWriter"] = None,
    ) -> Tuple[str, str]:
        """The ONE bounded intervention (fresh context, own tool kit,
        review recorded): (text, action). *writer* — the supervisor
        agent's llm writer for THIS conversation: when given, the calls
        run inside the caller's already-bound chain and the review
        persists keyed to the writer's context id (a first-class
        context row); None keeps the plain fail-soft call (chain_id
        NULL). Raises on provider failure — callers fail-soft."""
        prompts = await fetch_supervisor_prompts()
        kit = SupervisorToolKit(
            self.session.scope_id,
            descriptions=prompts.tool_descriptions)
        outcome = await intervene_on_failure(
            self.session.provider.chat_complete,
            goal=self.session.goal, rows=rows, failures=failures,
            model=self.session.model,
            system_prompt=prompts.system,
            builtin_tools=kit.builtin_tools(),
            tool_addendum=kit.prompt_addendum(),
            repair_eligible=repair_eligible,
            message_frame=prompts.intervention,
        )
        try:
            await PLAN_STORE.record_supervisor_review(
                self.session.scope_id, outcome.text,
                llm_context_id=getattr(writer, "context_id", None))
        except Exception as exc:  # noqa: BLE001 — fail-soft
            logger.warning("supervisor review persist failed: %s", exc)
        return outcome.text, outcome.action


def _none_verdicts(pending: List[Tuple[int, str]]) -> Dict[int, Verdict]:
    """The immediate no-action verdict dict (zero-LLM exits)."""
    return {sid: Verdict(action=ACTION_NONE) for sid, _ in pending}


class RepairWorker(SupervisorWorker):
    """The PAUSE WINDOW's decision — the classified REPAIR dispatch.
    ONE bounded agent call decides the batch; its ``Action:`` line is
    advisory until the code gates apply it: ``retry-step`` re-opens the
    failed row (a RATE-LIMIT class retry is THROTTLED inside the window
    before the hop re-runs), ``ignore-step`` settles the row skipped
    wearing the supervisor's note and the run resumes PAST it,
    ``no-action`` leaves the row failed. A retry verdict CARRIES the
    supervisor's INSTRUCTION (the bounded reply text) — the resumed
    node agent's resume context. Failures already asked-and-answered
    (the ``asked`` set — e.g. a retry budget spent) fast-path
    no-action with zero LLM.

    THE MODEL-UNREACHABLE FALLBACK (2026-11-06): when the ask itself
    fails TRANSIENTLY — the verdict call rides the same provider as the
    failed step, so a rate limit kills it too, immediately (zhipu 1302
    is not in the transport's retry codes) — the CODE issues the
    verdict instead of collapsing to no-action: every transient-class
    step failure gets its budget-gated retry-step (rate-limited ones
    throttled; ``should_retry`` still gates on the STEP's error, so a
    non-transient step failure is never smuggled through by a transient
    ask failure)."""

    async def handle(
        self, event: SupervisorEvent, *,
        writer: Optional["NodeRunWriter"] = None,
    ) -> WorkerOutcome:
        pending = [(ref.state_id, ref.error) for ref in event.failures]
        agent_spent = await self._decide(
            pending=pending, window_id=event.window_id, writer=writer)
        return WorkerOutcome(
            handled=[sid for sid, _ in pending],
            agent_spent=agent_spent)

    async def _decide(
        self,
        *,
        pending: List[Tuple[int, str]],
        window_id: Optional[str],
        writer: Optional["NodeRunWriter"] = None,
    ) -> bool:
        """The decision body — every exit resolves the window (a stale
        window's resolution is a no-op: its lane already fell
        through). Returns whether a supervisor LLM call was spent."""
        channel = self.session.channel
        limits = run_limits()
        # Ask-once bookkeeping: a sid the agent was already asked about
        # (and whose retry budget is spent) never spends another call.
        askable = [(sid, err) for sid, err in pending
                   if sid not in channel.asked
                   or channel.attempts.get(sid, 0)
                   < limits.supervisor_max_node_retries]
        if not askable:
            channel.resolve_window(window_id, _none_verdicts(pending))
            return False
        try:
            # The CHAIN read, never the one thread: a lane-signalled
            # failure is usually a CUSTOM/RESULT segment row — reading
            # the root langgraph thread alone woke the agent with an
            # EMPTY failure manifest over a plan-only tree (the live
            # fallback probe's supervisor reviews said exactly that).
            # The super binding widens the read to every thread of the
            # run.
            rows = await self.session._watched_states()
        except Exception as exc:  # noqa: BLE001 — cannot even frame it
            logger.warning("supervisor repair: state read failed: %s", exc)
            channel.resolve_window(window_id, _none_verdicts(pending))
            return False
        failed = [s for s in root_path_failures(rows)
                  if s.state_id in {sid for sid, _ in pending}]
        agent_spent = False
        # The verdict call's own failure class (set only when it failed
        # transiently — the code-fallback path below).
        verdict_exc_rate_limited = False
        # THE SYSTEM ONE LANE (decision/, 2026-11-11): when an engine
        # is injected, the calibrated jev read is the FAST PATH for the
        # common verdict — a CONFIDENT YES ("resume") applies the retry
        # without spending the generative ask. It can never VETO: a
        # below-floor or no read falls through to the ask lane, whose
        # richer judgment (retry vs ignore vs no-action) governs — the
        # 2026-11-12 probes measured the operational reads near 0.5
        # (noisy), and a decisive-no would have killed the repair
        # lane's settled policy (transient class + budget → retry; the
        # code fallback below codifies exactly that when no model can
        # answer).
        engine = self.session.decision_engine
        # THE CAP-CLASS LANE (repair.is_step_cap, 2026-11-15) SKIPS the
        # fast path: the resume template's question presumes a transient
        # error class ("the error class is transient...") — a step-cap
        # death would be read against the wrong premise, and the raised-
        # budget verdict is the generative ask's judgment anyway (the
        # serving contract's question texts stay byte-stable). The skip
        # costs nothing — the fast path can never veto, the ask lane
        # judges as before.
        if engine is not None and not is_step_cap(pending[0][1]):
            primary = failed[0] if failed else None
            # Primitive kwargs only — the supervisor package imports
            # nothing from langgraph at runtime (the layering rule);
            # the injected engine builds its own typed facts. The read is
            # DEFENDED (the loop's _engine_read discipline): an engine
            # predating decide_resume, a broken engine or a read exception
            # must never leave this window unresolved — the lane would
            # dead-wait its whole grace for nothing. An unavailable read
            # falls through to the ask lane (the fast path never vetoes).
            decision = None
            try:
                decision = await engine.decide_resume(
                    error_text=pending[0][1],
                    node_title=getattr(primary, "title", "") or "",
                    rate_limited=is_rate_limited(pending[0][1]),
                    attempts=channel.attempts.get(pending[0][0], 0),
                    max_node_retries=limits.supervisor_max_node_retries,
                    repairs_used=channel.repairs_used,
                    max_repairs_per_run=limits.supervisor_max_repairs_per_run,
                )
            except Exception as exc:  # noqa: BLE001 — the ask lane judges
                logger.warning(
                    "supervisor repair: jev resume read failed (%s) — the "
                    "generative ask judges", exc)
            if (decision is not None
                    and decision.ok and decision.action == ACTION_RETRY):
                action = decision.action
                # The raw read (probabilities/confidence) stays OFF the
                # resumed agent's context: it lives on the decision step
                # row the engine recorded (llm.deep_agent_aux_steps) and
                # the [System One verdict] review row below — the
                # instruction carries the ACTION only.
                instruction = (
                    "Resume the failed step from where it stopped; do "
                    "not restart the whole plan.")
                logger.info(
                    "supervisor repair: jev verdict %s p=%.2f (no "
                    "generative ask spent)", action, decision.confidence)
                # Observability parity with the ask lane: the verdict is
                # ONE supervisor-review context row (no LLM was spent).
                try:
                    await PLAN_STORE.record_supervisor_review(
                        self.session.scope_id,
                        f"[System One verdict] action={action} "
                        f"p={decision.confidence:.2f} ("
                        + ", ".join(f"{k}={v:.2f}" for k, v
                                    in decision.probabilities.items())
                        + f") model={decision.model}; error: "
                        + pending[0][1][:200],
                        llm_context_id=getattr(writer, "context_id",
                                               None))
                except Exception as exc:  # noqa: BLE001 — fail-soft
                    logger.warning(
                        "supervisor jev-verdict persist failed: %s", exc)
                return await self._apply_verdicts(
                    channel, limits, pending, askable, window_id,
                    verdict_action=action, instruction=instruction)
            logger.info(
                "supervisor repair: jev read not decisive (%s) — the "
                "generative ask judges",
                (getattr(decision, "note", "") or "")[:120])
        try:
            review, action = await self._intervene(
                rows=rows, failures=failed or [], repair_eligible=True,
                writer=writer)
            instruction = " ".join((review or "").split())[:600]
            agent_spent = True
        except Exception as exc:  # noqa: BLE001 — fail-soft, BUT the CODE
            # classifies the ask's own failure before giving up: the
            # verdict call rides the SAME provider as the failed step,
            # so under a rate limit it cannot even be asked — its own
            # call fails fast (zhipu code 1302 is NOT in the transport's
            # retry codes) and this except IS the rate-limit event.
            # Letting the ask failure flip a transient class to
            # no-action is exactly what left rate-limited nodes failed
            # instead of throttled and resumed (the throttle was dead
            # code in the scenario it was built for). When the model is
            # unreachable the code issues the verdict itself — the
            # structural policy the model is taught: a transient class
            # retries (budget-gated in the loop below via should_retry;
            # should_retry still reads the STEP's error, so a
            # non-transient step failure can never be smuggled through
            # by a transient ask failure), a rate-limit one throttled.
            # Anything else keeps no-action.
            exc_text = f"{type(exc).__name__}: {exc}"
            if not is_transient(exc_text):
                logger.warning("supervisor intervention failed: %s", exc)
                channel.resolve_window(window_id, _none_verdicts(pending))
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
        return await self._apply_verdicts(
            channel, limits, pending, askable, window_id,
            verdict_action=action, instruction=instruction,
            agent_spent=agent_spent,
            fallback_rate_limited=verdict_exc_rate_limited)

    async def _apply_verdicts(
        self,
        channel, limits,
        pending: List[Tuple[int, str]],
        askable: List[Tuple[int, str]],
        window_id: Optional[str],
        *,
        verdict_action: str,
        instruction: str,
        agent_spent: bool = False,
        fallback_rate_limited: bool = False,
    ) -> bool:
        """The SHARED verdict application (the jev and the ask paths
        land here): per-sid budget/structural gates (``should_retry`` —
        the verdict is advisory until it passes), the reopen/ignore
        writes, the rate-limit throttle, the window resolution.
        Returns whether a supervisor LLM call was actually spent.

        THE LATE-VERDICT GUARD (2026-11-13): the window this decision
        belongs to may have been ABANDONED by the lane (its grace
        expired and it fell through to the failure semantics — the
        doomed-tail settle and the route to finalize may already have
        run). The review stands (the diagnosis row exists either way
        and the asked/handled marks keep the sweep from repeating it),
        but the row-mutating writes are refused: a reopen past finalize
        would strand a pending row nothing will ever re-run (a zombie
        live until the boot sweep), and an ignore would overwrite an
        already-reopened row's audit. A None window id (direct
        callers/tests without a lane window) keeps the ungated legacy
        contract.

        THE THROTTLE-BEFORE-WRITE ORDERING (2026-11-13): the verdicts
        are DECIDED first (the pure should_retry pass), then the
        rate-limit cooldown sleeps, then — and only then — the
        reopen/ignore writes land. The old order (write, sleep, resolve)
        could waste the retry: a sleep outlasting the lane's remaining
        grace let the lane abandon the window and settle the
        just-reopened rows as skipped. The window is re-checked after
        the sleep; every row write is refused when the lane is gone."""
        window_live = not (window_id is not None
                           and not channel.is_open(window_id))
        # Pass 1 — the pure decision: which sid retries (the structural
        # gate), which is refused. No writes yet — the throttle must land
        # first, on a state-set still untouched.
        decided: Dict[int, str] = {}
        rate_limited_retries = 0
        for sid, err in pending:
            action_sid = (verdict_action if (sid, err) in askable
                          else ACTION_NONE)
            if action_sid == ACTION_RETRY:
                if not window_live or not should_retry(channel, sid, err,
                                                       verdict_action):
                    action_sid = ACTION_NONE
                elif is_rate_limited(err):
                    rate_limited_retries += 1
            elif action_sid == ACTION_IGNORE and not window_live:
                action_sid = ACTION_NONE
            decided[sid] = action_sid
        if ((rate_limited_retries or fallback_rate_limited)
                and any(a == ACTION_RETRY for a in decided.values())
                and limits.supervisor_retry_throttle_sec > 0):
            # The lane is paused in its window — the cooldown rides it:
            # hold the re-run back so the hot account cools first. Either
            # the STEP's error was rate-limited (the model said retry) or
            # the verdict call itself just got rate-limited (the provider
            # is hot right now — the code fallback decided the retry); a
            # retry must at least have been DECIDED (a budget-refused
            # batch re-runs nothing, so nothing to hold).
            logger.info(
                "supervisor repair: throttling rate-limit retry %ss",
                limits.supervisor_retry_throttle_sec)
            await asyncio.sleep(limits.supervisor_retry_throttle_sec)
            if window_id is not None and not channel.is_open(window_id):
                # The lane's grace expired mid-sleep — it fell through to
                # the failure semantics already. The review stands (the
                # asked/handled marks below keep the sweep from repeating
                # it) but every row write is refused.
                logger.info(
                    "supervisor repair: window abandoned during the "
                    "throttle — no row writes")
                window_live = False
                decided = {sid: ACTION_NONE for sid in decided}
        # Pass 2 — the writes, under the (re-checked) window guard.
        verdicts: Dict[int, Verdict] = {}
        for sid, err in pending:
            action_sid = decided[sid]
            if action_sid == ACTION_RETRY:
                try:
                    if await PLAN_STORE.reopen_state(
                            sid, thread_id=self.session.scope_id,
                            note=instruction[:160]):
                        channel.attempts[sid] = (
                            channel.attempts.get(sid, 0) + 1)
                        channel.repairs_used += 1
                        if is_step_cap(err):
                            # The cap lane's own budget (should_retry
                            # reads it) — one raised-budget re-open per
                            # run by default.
                            channel.cap_retries_used += 1
                    else:
                        action_sid = ACTION_NONE
                except Exception as exc:  # noqa: BLE001 — fail-soft
                    logger.warning(
                        "supervisor reopen failed (%s): %s", sid, exc)
                    action_sid = ACTION_NONE
            elif action_sid == ACTION_IGNORE:
                try:
                    if not await PLAN_STORE.ignore_state(
                            sid, thread_id=self.session.scope_id):
                        action_sid = ACTION_NONE  # row already moved on
                except Exception as exc:  # noqa: BLE001 — fail-soft
                    logger.warning(
                        "supervisor ignore failed (%s): %s", sid, exc)
                    action_sid = ACTION_NONE
            elif action_sid == ACTION_NONE and window_live:
                # THE FAILURE FORK (2026-11-15): a no-action verdict
                # leaves the row failed — record the failed attempt as
                # the red dead-end branch under the step (the fork
                # action on the UI), so the canvas shows the failure
                # forked off the main path instead of a bare red lane
                # node. Fail-soft; the row moving on records nothing.
                try:
                    await PLAN_STORE.record_failure_marker(
                        sid, title="supervisor no-action")
                except Exception as exc:  # noqa: BLE001 — fail-soft
                    logger.warning(
                        "supervisor failure-marker failed (%s): %s",
                        sid, exc)
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
        channel.resolve_window(window_id, verdicts)
        return agent_spent  # was a supervisor LLM call actually spent?


class ObserveWorker(SupervisorWorker):
    """The belt's observe review — the classified REVIEW dispatch (the
    former inline belt block in the poll loop). A root-path failure the
    lane never signalled (plan row, a review member) gets its observe
    review early — ONE attempt per failure (the attempted set is
    marked BEFORE the attempt: a failed review under a throttled
    provider must not retry-storm every interval; the final sweep still
    reviews anything left unreviewed). OBSERVE MODE: diagnoses and
    recommends, never applies, no veto."""

    async def handle(
        self, event: SupervisorEvent, *,
        writer: Optional["NodeRunWriter"] = None,
    ) -> WorkerOutcome:
        channel = self.session.channel
        # Code filter first: sids the repair path already asked/reviewed
        # (a belt event can coexist with a window over the same row in
        # one drain) never spend a second review.
        sids = [ref.state_id for ref in event.failures
                if ref.state_id not in channel.asked
                and ref.state_id not in channel.handled]
        if not sids:
            return WorkerOutcome(note="already handled — dropped")
        channel.attempted.update(sids)
        try:
            rows = await self.session._watched_states()
            failed = [s for s in root_path_failures(rows)
                      if s.state_id in set(sids)]
            await self._intervene(
                rows=rows, failures=failed or [], repair_eligible=False,
                writer=writer)
        except Exception as exc:  # noqa: BLE001 — the sweep still reviews
            logger.warning("supervisor early review failed: %s", exc)
            return WorkerOutcome(handled=[], agent_spent=False,
                                 note=f"review failed: {exc}")
        channel.handled.update(sids)
        return WorkerOutcome(handled=sids, agent_spent=True)


class SweepWorker(SupervisorWorker):
    """The final sweep — the classified SWEEP dispatch (the former
    ``SupervisorSession.sweep``, the old post-run watchdog tail). IDLE
    on healthy runs: an empty failure set just closes the watching
    marker completed with zero LLM calls (no context minted, no
    registry item, no review row, and zero prompt fetches — the skills
    self-fetch only run on the waking path). Only a ROOT-PATH failure
    wakes the model: a bounded observe-mode intervention in its OWN
    fresh context (request chain bound, review persisted as the kind
    supervisor-review row keyed to that context) — it diagnoses the
    error and recommends whether the plan/graph topology needs an
    update, never applies one. Never raises (every rung fail-soft —
    the run's recorded result is already final)."""

    async def handle(
        self, event: SupervisorEvent, *,
        writer: Optional["NodeRunWriter"] = None,
        skip_state_ids: "frozenset[int]" = frozenset(),
    ) -> WorkerOutcome:
        session = self.session
        if session.marker is None:
            if session.writer_factory is None:
                return WorkerOutcome(note="no marker factory")
            try:
                session.marker = session.writer_factory(False)
                await session.marker.begin(None)
            except Exception as exc:  # noqa: BLE001 — never into the picker
                logger.warning(
                    "supervisor sweep: marker begin failed: %s", exc)
                session.marker = None
                return WorkerOutcome(note="marker begin failed")
        marker = session.marker
        try:
            rows = await session._watched_states()
        except Exception as exc:  # noqa: BLE001 — idle-close the marker
            logger.warning(
                "supervisor sweep: state read failed: %s", exc)
            try:
                await marker.finish(
                    "failed", error=f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 — the marker is best-effort
                pass
            return WorkerOutcome(note="state read failed")
        failures = [s for s in root_path_failures(rows)
                    if s.state_id not in skip_state_ids]
        if not failures:
            # IDLE — the healthy common case: zero LLM, zero context.
            try:
                await marker.finish("completed")
            except Exception:  # noqa: BLE001 — best-effort
                pass
            return WorkerOutcome()
        logger.info("supervisor sweep: %d root-path failure(s) — "
                    "intervening", len(failures))
        try:
            sweep_writer = session.writer_factory(True)
            await sweep_writer.begin(None)
        except Exception as exc:  # noqa: BLE001 — the sweep is best-effort
            logger.warning(
                "supervisor sweep: writer begin failed: %s", exc)
            try:
                await marker.finish(
                    "failed", error=f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 — best-effort
                pass
            return WorkerOutcome(note="sweep writer begin failed")
        review: Optional[str] = None
        error: Optional[str] = None
        try:
            with bind_request_chain(sweep_writer.context_id):
                text, _action = await self._intervene(
                    rows=rows, failures=failures, repair_eligible=False,
                    writer=sweep_writer)
                review = text
        except Exception as exc:  # noqa: BLE001 — observe mode never vetoes
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("supervisor intervention failed: %s", exc)
        finally:
            end_request_chain(sweep_writer.context_id)
        try:
            await sweep_writer.finish(
                "failed" if error is not None else "completed",
                n_llm_calls=0 if error is not None else 1,
                error=error, answer_chars=len(review or ""))
        except Exception:  # noqa: BLE001 — best-effort
            pass
        return WorkerOutcome(
            handled=[s.state_id for s in failures],
            agent_spent=error is None)


# The dispatch registry — EventClass → the worker sub-agent the picker
# launches. A new supervision capability registers here (a new
# EventClass in classify.py + one subclass above); the picker's loop
# and the lanes never change.
WORKER_FACTORIES: Dict[EventClass, Type[SupervisorWorker]] = {
    EventClass.REPAIR: RepairWorker,
    EventClass.REVIEW: ObserveWorker,
    EventClass.SWEEP: SweepWorker,
}
