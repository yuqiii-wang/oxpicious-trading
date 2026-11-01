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
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Set

# Per-subscriber bound: deep enough to absorb a plan persist (one event
# per planned step) plus a settle burst, small enough that drop-oldest
# engages fast on a stalled consumer.
_QUEUE_MAX = 256

_subscribers: Set["asyncio.Queue[Dict[str, Any]]"] = set()


def now_iso() -> str:
    """The event timestamp — UTC ISO-8601 (operational marker, not
    display text; the UI renders its own local times from the DB)."""
    return datetime.now(timezone.utc).isoformat()


def encode(event: Dict[str, Any]) -> str:
    """One event as a compact JSON text frame (the /ws wire format)."""
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def publish(event: Dict[str, Any]) -> None:
    """Announce one event to every subscriber. Never blocks the caller
    (a store write path inside a run): a full queue drops its OLDEST
    event — the newest state wins for a consumer that fell behind."""
    for queue in list(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


def subscribe() -> "asyncio.Queue[Dict[str, Any]]":
    """Register one subscriber's bounded queue (the /ws handler's relay
    loop drains it frame by frame)."""
    queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=_QUEUE_MAX)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: "asyncio.Queue[Dict[str, Any]]") -> None:
    """Drop a subscriber (its /ws connection ended) — a no-op for a
    queue that is already gone."""
    _subscribers.discard(queue)
