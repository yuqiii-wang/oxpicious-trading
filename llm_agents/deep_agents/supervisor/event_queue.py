"""llm_agents.deep_agents.supervisor.event_queue — the SUPERVISOR's
EVENT QUEUE (2026-11-11): the queue-picker-worker refactor of the
supervision loop. The former RepairChannel handshake held ONE pending
slot plus three raw asyncio.Events — a second lane signal while a
window was open clobbered the first, and every detection path (lane
signal, DB belt, runner close) carried its own bespoke handling inside
the poller. Now every notable run event lands HERE as a typed payload
and the picker (``poller.SupervisorSession.run``) drains, classifies
(``classify.py``) and launches the matching worker sub-agent
(``workers.py``) — the Super-agent shape the blueprint endorses: one
router, many workers; deliver events, not prompts.

PRODUCERS (all fail-soft, never block the lane):
* the step lane / review join — ``RepairChannel.signal_failures`` (a
  NODE_FAILED event carrying the open window's id; the lane-side
  facade stays in ``repair.py`` — the lanes' two calls are unchanged);
* the session's interval belt — a FAILURE_DETECTED event per fresh
  unsignalled root-path failure;
* the runner's close — ``RepairChannel.close`` offers the terminal
  RUN_SETTLED event (the picker's sweep dispatch).

COALESCING: offers merge into a queued event of the same kind —
belt discoveries dedupe by state_id (newest error text wins) and a
duplicate RUN_SETTLED collapses — so a burst of producers produces one
drainable batch, never a lost or doubled dispatch. NODE_FAILED events
are NEVER merged (each carries its own window id) — at most one window
is open at a time and a superseded one is dropped at dispatch.

Pure module, stub-testable (asyncio primitives + pydantic only; it
must NOT import ``repair`` — repair imports this, and the Verdict type
is referenced by string annotation only).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # string-annotated only — repair imports this module
    from llm_agents.deep_agents.supervisor.repair import Verdict


class EventKind(str, Enum):
    """The supervision event vocabulary — what happened, not what to
    do (the classification into a worker launch lives in classify.py,
    code-first)."""

    NODE_FAILED = "node_failed"          # a lane window opened (urgent)
    FAILURE_DETECTED = "failure_detected"  # the belt found a failure
    RUN_SETTLED = "run_settled"          # the runner closed the run


class FailureRef(BaseModel):
    """One failed node reference — the state id plus the error text the
    classifier reads (the same pair the lane signalled, typed)."""

    model_config = ConfigDict(frozen=True)

    state_id: int
    error: str = ""


class SupervisorEvent(BaseModel):
    """One queued supervision event — the picker's unit of work. A
    NODE_FAILED event carries the ``window_id`` whose future the
    RepairWorker resolves; a FAILURE_DETECTED event is belt-discovered
    (no window — the lane already routed past); RUN_SETTLED is
    terminal."""

    model_config = ConfigDict(frozen=True)

    kind: EventKind
    failures: List[FailureRef] = Field(default_factory=list)
    source: str = ""                     # "lane" | "belt" | "runner"
    window_id: Optional[str] = None
    created: float = Field(default_factory=time.time)


class RepairWindow:
    """ONE lane pause window — the lane's ``await_verdicts`` waits this
    window's future; the RepairWorker resolves it with the verdict
    dict. The id is the correlation key riding the NODE_FAILED event.
    A window resolved with ``{}`` (supersede, close, or an already-
    answered race) reads to the lane exactly as the old timeout did:
    the pre-window failure semantics apply untouched."""

    def __init__(self, failures: Tuple[Tuple[int, str], ...]) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.failures = tuple(failures)
        self.future: "asyncio.Future[Dict[int, Verdict]]" = (
            asyncio.get_running_loop().create_future())

    def resolve(self, verdicts: Dict[int, "Verdict"]) -> None:
        """Wake the waiting lane (a done/cancelled future — the lane
        timed out — ignores the resolution; the DB writes the worker
        already applied stand either way, as before)."""
        if not self.future.done():
            self.future.set_result(dict(verdicts))


# The tray's cap — a run cannot produce more distinct supervision
# events than it has nodes; the bound is belt-and-suspenders with the
# same drop-oldest policy the UI event bus uses (never block a write
# path).
MAX_QUEUED_EVENTS = 64


class SupervisorEventQueue:
    """The bounded coalescing event tray between the producers and the
    picker. Single consumer (the picker) by construction; any number of
    producers. ``offer`` never blocks and never loses a NODE_FAILED
    event to the cap (drop-oldest prefers review/sweep events); the
    picker's ``drain`` returns every queued event as one batch."""

    def __init__(self) -> None:
        self._events: List[SupervisorEvent] = []
        self._wake = asyncio.Event()
        self._closed = False

    # ---- producer side ----------------------------------------------
    def offer(self, event: SupervisorEvent) -> None:
        """Coalesce + enqueue (synchronous — a producer never awaits).
        Merging: same-kind belt events dedupe by state_id (the newest
        error text wins), a second RUN_SETTLED collapses into the
        first, NODE_FAILED events stay distinct (one window each)."""
        if self._closed:
            return
        merged = self._merge(event)
        if merged is not None:
            return
        self._events.append(event)
        while len(self._events) > MAX_QUEUED_EVENTS:
            self._drop_oldest()
        self._wake.set()

    def _merge(self, event: SupervisorEvent) -> Optional[SupervisorEvent]:
        """Fold *event* into a queued same-kind event when the kinds
        coalesce; None = append as a distinct entry."""
        for i, queued in enumerate(self._events):
            if queued.kind is not event.kind:
                continue
            if event.kind is EventKind.RUN_SETTLED:
                return queued  # terminal — already queued, idempotent
            if event.kind is EventKind.NODE_FAILED:
                return None    # per-window — never merged
            # FAILURE_DETECTED — dedupe by state_id, newest error wins
            by_sid = {f.state_id: f for f in queued.failures}
            for ref in event.failures:
                by_sid[ref.state_id] = ref
            self._events[i] = queued.model_copy(update={
                "failures": list(by_sid.values())})
            self._wake.set()
            return self._events[i]
        return None

    def _drop_oldest(self) -> None:
        """The cap's victim: the oldest event that is NOT a lane window
        (a dropped NODE_FAILED would strand its window unresolved —
        the lane would wait out its grace for nothing)."""
        for i, queued in enumerate(self._events):
            if queued.kind is not EventKind.NODE_FAILED:
                del self._events[i]
                return
        del self._events[0]

    # ---- picker side --------------------------------------------------
    @property
    def has_events(self) -> bool:
        return bool(self._events)

    @property
    def closed(self) -> bool:
        return self._closed

    async def drain(self, *, timeout_sec: float) -> List[SupervisorEvent]:
        """The picker's wait: every queued event as one batch. Returns
        immediately when the tray holds events; on a closed-and-empty
        tray (the loop's exit check); else waits up to *timeout_sec*
        for the next offer — a timeout that races an offer still
        returns the offered batch (the final swap is unconditional)."""
        if self._closed and not self._events:
            return []
        if not self._events:
            try:
                await asyncio.wait_for(self._wake.wait(),
                                       timeout=max(0.0, timeout_sec))
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
        events, self._events = self._events, []
        return events

    # ---- lifecycle ----------------------------------------------------
    def close(self) -> None:
        """The run is over: queued lane-window events are purged (their
        windows are released with {} by the channel), the terminal
        RUN_SETTLED event wakes the picker for its final sweep
        dispatch, and later offers are ignored. An already-closed
        queue is a no-op (idempotent)."""
        if self._closed:
            return
        self._events = [e for e in self._events
                        if e.kind is not EventKind.NODE_FAILED]
        self.offer(SupervisorEvent(kind=EventKind.RUN_SETTLED,
                                   source="runner"))
        self._closed = True
        self._wake.set()
