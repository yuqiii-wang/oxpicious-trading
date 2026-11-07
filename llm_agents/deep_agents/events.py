"""llm_agents.deep_agents.events — the in-process deep-agent event bus.

The publish side of the daemon's /ws WebSocket channel (server.py): every
thread/state mutation the planning store writes is announced here, and
each connected subscriber (the data_viz API bridge's ONE upstream socket)
receives the announcements as JSON — the push transport that carries
deep-agent state updates to the UI without discover-by-polling (the
Generate click's Summary History record and the live thread graph both
open on the first event now, not on the next poll tick).

Events are NOTIFICATIONS, not state transfers: each carries the event
type, the thread it concerns and the key identity fields (state id,
status, ordinal) — the browser responds by re-reading the thread/run
endpoints it already has, so the DB stays the single source of truth.
No replay, no persistence: a subscriber that connects mid-run catches up
through those same reads (the /ws handshake's hello carries the
in-flight thread ids), and an event lost to a burst only delays the
refresh to the UI's next scheduled poll.

Fan-out is per-subscriber BOUNDED queues with drop-oldest: a dead or
stalled consumer can never block the run's own bookkeeping — publish is
a synchronous ``put_nowait`` from the store's write paths, which all run
inside the daemon's one event loop (the runner nodes, the plan tools,
the subagent hooks share the process; the CLI path publishes to zero
subscribers, a no-op).

Every domain event is a typed ``DeepAgentEvent`` (pydantic — the
envelope that replaced the loose per-site dicts, 2026-11-06); the /ws
PROTOCOL frames (``hello``/``heartbeat``/``pong`` in server.py) stay
loose dicts — they are transport, not domain events. Serialization is
``model_dump(exclude_none=True)``: a field the publisher did not set is
ABSENT from the wire frame (previously some sites passed explicit
``None`` — e.g. ``parent_state_id: null`` on the two top-level
state_created events; the key is now simply absent, which reads the
same on the JS side).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Set, Union

from pydantic import BaseModel, Field

# Per-subscriber bound: deep enough to absorb a plan persist (one event
# per planned step) plus a settle burst, small enough that drop-oldest
# engages fast on a stalled consumer.
_QUEUE_MAX = 256


class EventType(str, Enum):
    """The domain event vocabulary (the /ws notification types)."""

    THREAD_STARTED = "thread_started"
    STATE_CREATED = "state_created"
    STATE_UPDATED = "state_updated"
    NODE_FINISHED = "node_finished"
    STATES_SETTLED = "states_settled"
    RESULT_RECORDED = "result_recorded"
    THREAD_SETTLED = "thread_settled"


def now_iso() -> str:
    """The event timestamp — UTC ISO-8601 (operational marker, not
    display text; the UI renders its own local times from the DB)."""
    return datetime.now(timezone.utc).isoformat()


class DeepAgentEvent(BaseModel):
    """One /ws notification: the event type, its thread and the key
    identity fields the UI's refresh paths need. Only the fields a
    publisher sets ride the wire frame (``exclude_none``)."""

    type: EventType
    thread_id: str
    ts: str = Field(default_factory=now_iso)
    # thread_started / thread_settled: the chain root (== the root plan
    # thread's own id for a new run).
    super_thread_id: Optional[str] = None
    # thread_settled only: the run outcome ("failed" on the error path).
    outcome: Optional[str] = None
    # state_created / state_updated / node_finished identity.
    state_id: Optional[int] = None
    node: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    ordinal: Optional[int] = None
    parent_state_id: Optional[int] = None
    # states_settled only: how many rows the settle touched (sites that
    # do not count omit it).
    count: Optional[int] = None


class EventBus:
    """The in-process event bus: the subscriber registry + the bounded
    fan-out. One process-wide instance (``EVENT_BUS``) — the daemon's
    single event loop shares it across the runner nodes, the plan tools
    and the subagent hooks."""

    def __init__(self, queue_max: int = _QUEUE_MAX) -> None:
        self._queue_max = queue_max
        self._subscribers: Set["asyncio.Queue[Dict[str, Any]]"] = set()

    @property
    def subscriber_count(self) -> int:
        """Live subscribers (the /ws handshake's in-flight count)."""
        return len(self._subscribers)

    @staticmethod
    def _as_dict(event: Union[DeepAgentEvent, Dict[str, Any]]) -> Dict[str, Any]:
        """One event as its wire dict — a DeepAgentEvent dumps with the
        unset fields dropped; a plain dict (the /ws protocol frames)
        passes through."""
        if isinstance(event, BaseModel):
            return event.model_dump(exclude_none=True)
        return event

    def publish(self, event: Union[DeepAgentEvent, Dict[str, Any]]) -> None:
        """Announce one event to every subscriber. Never blocks the caller
        (a store write path inside a run): a full queue drops its OLDEST
        event — the newest state wins for a consumer that fell behind."""
        payload = self._as_dict(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def subscribe(self) -> "asyncio.Queue[Dict[str, Any]]":
        """Register one subscriber's bounded queue (the /ws handler's relay
        loop drains it frame by frame)."""
        queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=self._queue_max)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[Dict[str, Any]]") -> None:
        """Drop a subscriber (its /ws connection ended) — a no-op for a
        queue that is already gone."""
        self._subscribers.discard(queue)


# The process-wide bus (one daemon event loop; the CLI path publishes to
# zero subscribers, a no-op).
EVENT_BUS = EventBus()


def encode(event: Union[DeepAgentEvent, Dict[str, Any]]) -> str:
    """One event as a compact JSON text frame (the /ws wire format)."""
    return json.dumps(
        EventBus._as_dict(event), ensure_ascii=False, separators=(",", ":"))


def publish(event: Union[DeepAgentEvent, Dict[str, Any]]) -> None:
    """Module-level delegate over :data:`EVENT_BUS` — the store/state
    write paths' one-line announce."""
    EVENT_BUS.publish(event)


def subscribe() -> "asyncio.Queue[Dict[str, Any]]":
    """Module-level delegate over :data:`EVENT_BUS`."""
    return EVENT_BUS.subscribe()


def unsubscribe(queue: "asyncio.Queue[Dict[str, Any]]") -> None:
    """Module-level delegate over :data:`EVENT_BUS`."""
    EVENT_BUS.unsubscribe(queue)
