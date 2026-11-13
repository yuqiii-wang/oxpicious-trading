"""llm_agents.deep_agents.supervisor.poller — the SUPERVISOR SESSION,
now THE PICKER (2026-11-11): the per-run backend job the runner starts
beside every graph, sharing no node, edge or data lane with it. The
former poll-decide loop became the event-queue architecture:

* DRAIN — ``run`` blocks on the event queue (``event_queue.py``):
  every queued event as one batch, waking instantly on a producer's
  offer (the lane pause window, the belt scan) or after the poll
  interval.
* CLASSIFY — each batch is sorted by class priority (the paused lane's
  REPAIR jumps ahead of belt REVIEWs and the terminal SWEEP) and every
  event maps to its worker class in pure code (``classify.py``) — a
  superseded window's event is dropped (its lane already fell
  through).
* LAUNCH — the classified event dispatches its worker sub-agent
  (``workers.py``'s registry): the RepairWorker decides the pause
  window (code gates first, then ONE bounded tool-carrying
  intervention; the model-unreachable code fallback issues retry
  verdicts when the ask itself dies transiently), the ObserveWorker
  reviews belt-discovered failures (zero veto), the SweepWorker runs
  the final sweep on the terminal event. Every waking dispatch runs
  under the supervisor agent's OWN llm writer + bound chain — a
  first-class context row (the sweep manages its own writer lifecycle).
* PRODUCE — on every idle interval the session runs the belt scan
  itself: fresh unsignalled root-path failures (plan row, a review
  member) become FAILURE_DETECTED events for the ObserveWorker.

Idle on healthy runs: one cheap DB poll per interval, zero LLM; the
terminal RUN_SETTLED event (offered by ``RepairChannel.close``) is the
sweep's trigger — the old post-run tail, absorbed into the queue.

ONE class since 2026-11-06: the session's provider / channel / thread
ids / goal / model / writer factory are the object's fields, and the
picker loop, the belt producer and the agent-context wrapper are its
methods (the decisions live in the workers now). THE SUPER BINDING:
``super_thread_id`` (the three-graph chain's root id) is the session's
OWN id — every keyed artifact (review rows, kit scope, reopen/ignore
events, the sweep) resolves through ``scope_id``, and every state read
widens to the run's WHOLE CHAIN; None keeps the plain thread read
(legacy callers/tests). Never raises into the runner.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.store import PLAN_STORE, StateRow
from llm_agents.deep_agents.supervisor.classify import (
    CLASS_PRIORITY, EventClass, classify_event,
)
from llm_agents.deep_agents.supervisor.engine import root_path_failures
from llm_agents.deep_agents.supervisor.event_queue import (
    EventKind, FailureRef, SupervisorEvent,
)
from llm_agents.deep_agents.supervisor.repair import RepairChannel
from llm_agents.deep_agents.supervisor.workers import (
    WORKER_FACTORIES, SupervisorWorker,
)
from llm_agents.deep_agents.core.provider import ZhipuChatProvider

if TYPE_CHECKING:  # no runtime langgraph import — the runner injects
    from langgraph.store.base import BaseStore
    from llm_agents.deep_agents.langgraph.state_store import NodeRunWriter

logger = logging.getLogger("deep_agents.supervisor")

# The runner-side writer factory: (llm: bool) -> a NodeRunWriter for
# THIS run's supervisor namespace (the marker is the llm=False form).
WriterFactory = Callable[[bool], "NodeRunWriter"]


class SupervisorSession:
    """The per-run supervisor session — THE PICKER (2026-11-11): the
    event-queue drain loop, the belt producer and the worker launcher
    as methods of ONE object holding the run's binding (provider,
    channel, thread ids, goal, writer factory) and the worker
    instances (built from ``WORKER_FACTORIES``). Never raises into the
    runner."""

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
        decision_engine: Optional[Any] = None,
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
        # The SYSTEM ONE lane (decision/, 2026-11-11): when an engine is
        # injected the RepairWorker asks the resident jev model FIRST
        # and only falls back to the generative ask when the read is
        # unavailable or below its confidence floor. None (tests,
        # probes, knob off) = the legacy ask lane only.
        self.decision_engine = decision_engine
        # THE WORKERS — one instance per classified event class; the
        # dispatch registry (workers.WORKER_FACTORIES) is the extension
        # point for new supervision capabilities.
        self._workers: Dict[EventClass, SupervisorWorker] = {
            cls: factory(self) for cls, factory in
            WORKER_FACTORIES.items()}

    # ------------------------------------------------------------------
    # State reads
    # ------------------------------------------------------------------
    async def _watched_states(self) -> List[StateRow]:
        """The rows the session watches: the run's WHOLE CHAIN when the
        super id is known (every thread of the run), else the one thread
        (legacy callers/tests)."""
        if self.super_thread_id:
            return await PLAN_STORE.list_super_states(self.super_thread_id)
        return await PLAN_STORE.list_thread_states(self.thread_id)

    # ------------------------------------------------------------------
    # The picker loop
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """The per-run PICKER loop — drain → classify → launch, until
        the queue closes (the runner owns the task; closing the channel
        offers the terminal RUN_SETTLED event whose dispatch IS the
        final sweep). On every idle interval the belt scan produces the
        FAILURE_DETECTED events the lane never signalled. Never raises
        (CancelledError returns — a cancelled run is settled by the
        runner's own paths; the old tail never ran on cancel either)."""
        limits = run_limits()
        try:
            while True:
                events = await self.channel.queue.drain(
                    timeout_sec=limits.supervisor_poll_interval_sec)
                if not events:
                    if self.channel.closed:
                        break
                    await self._belt_scan()
                    continue
                for event in self._prioritized(events):
                    await self._dispatch(event)
        except asyncio.CancelledError:  # runner's finally — run is over
            return

    def _prioritized(
            self, events: List[SupervisorEvent]) -> List[SupervisorEvent]:
        """One drain's batch in dispatch order: the paused lane's
        REPAIR jumps ahead (its grace window is the run's latency),
        belt REVIEWs follow, the terminal SWEEP runs last; a
        superseded window's event is dropped (its lane wait already
        fell through — a stale window id would never resolve)."""
        live = []
        for event in events:
            if (event.kind is EventKind.NODE_FAILED
                    and not self.channel.is_open(event.window_id)):
                continue
            live.append(event)
        return sorted(live, key=lambda e: (
            CLASS_PRIORITY[classify_event(e)], e.created))

    async def _dispatch(self, event: SupervisorEvent) -> None:
        """One classified event → its worker sub-agent. Repair/Review
        dispatches run under the supervisor agent's own llm writer +
        bound chain (a first-class context row); the sweep manages its
        own writer lifecycle inside the worker."""
        cls = classify_event(event)
        worker = self._workers.get(cls)
        if worker is None:
            logger.warning(
                "supervisor picker: no worker registered for %s — "
                "dropping %s", cls, event.kind)
            return
        if cls is EventClass.SWEEP:
            with contextlib.suppress(Exception):
                await worker.handle(event)
            return
        await self._run_worker_under_agent(worker, event)

    async def _run_worker_under_agent(
        self, worker: SupervisorWorker, event: SupervisorEvent,
    ) -> None:
        """A waking dispatch run as the SUPERVISOR AGENT's own
        conversation: its llm writer begun, chain bound, the worker
        inside, the node item closed — a first-class context row (the
        pre-agent poller ran these with chain_id NULL).
        *intervention_agent* is None-able for the plain fail-soft path
        (tests)."""
        agent = self.intervention_agent
        if agent is None:
            with contextlib.suppress(Exception):
                await worker.handle(event)
            return
        # The agent's llm-writer hook (the supervisor's ctor factory
        # defaults llm=False for the marker — interventions need
        # contexts).
        new_writer = getattr(agent, "new_llm_writer", None)
        if new_writer is None:
            new_writer = agent.new_writer
        writer = new_writer()
        await agent.begin_node(writer)
        try:
            with agent.chain(writer):
                outcome = await worker.handle(event, writer=writer)
        except Exception as exc:  # noqa: BLE001 — the worker self-guards
            logger.warning("supervisor worker context failed: %s", exc)
            await agent.close_node(
                writer, "failed", error=f"{type(exc).__name__}: {exc}",
                n_llm_calls=0)
            return
        await agent.close_node(
            writer, "completed",
            n_llm_calls=1 if outcome.agent_spent else 0)

    # ------------------------------------------------------------------
    # The belt producer
    # ------------------------------------------------------------------
    async def _belt_scan(self) -> None:
        """The interval belt — the REVIEW path's producer: a root-path
        failure the lane never signalled (the plan row, a review
        member) becomes a FAILURE_DETECTED event. The scan filter
        excludes failures already reviewed (handled), already
        attempted (one review attempt per failure — a failed review
        under a throttled provider must not retry-storm every
        interval; the final sweep still reviews anything left) and the
        open window's sids (their verdict belongs to the lane path).
        Fail-soft — a scan failure just waits for the next interval."""
        with contextlib.suppress(Exception):
            rows = await self._watched_states()
            channel = self.channel
            fresh = [s for s in root_path_failures(rows)
                     if s.state_id not in channel.handled
                     and s.state_id not in channel.attempted
                     and s.state_id not in channel.pending_ids]
            if fresh:
                channel.queue.offer(SupervisorEvent(
                    kind=EventKind.FAILURE_DETECTED,
                    failures=[FailureRef(state_id=s.state_id,
                                         error=s.error or "")
                              for s in fresh],
                    source="belt"))

    # ------------------------------------------------------------------
    # The final sweep (kept for direct callers/tests)
    # ------------------------------------------------------------------
    async def sweep(
        self, *, skip_state_ids: "frozenset[int]" = frozenset(),
    ) -> None:
        """The watchdog's tail sweep — the supervisor's post-graph
        observe pass, keyed through the SUPER id. Delegates to the
        SweepWorker (the classified SWEEP dispatch); IDLE on healthy
        runs: the trigger is :func:`engine.root_path_failures` — a pure
        filter over the settled node states — and an empty result just
        closes the watching marker completed with zero LLM calls.
        *skip_state_ids* — the failures the mid-run session already
        reviewed (one review per failure, not one per lane). Never
        raises."""
        with contextlib.suppress(Exception):
            await self._workers[EventClass.SWEEP].handle(
                SupervisorEvent(kind=EventKind.RUN_SETTLED,
                                source="direct"),
                skip_state_ids=skip_state_ids)
