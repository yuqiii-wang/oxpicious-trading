"""llm_agents.deep_agents.supervisor.classify — the PICKER's pure code
classifier (2026-11-11): every drained event maps to an EventClass,
and the EventClass selects the worker sub-agent the picker launches
(``workers.py``'s registry). Code filters first — an event's class
NEVER depends on a model call; the finer gates (transient vs not, the
retry budgets) stay inside the RepairWorker where the zero-LLM fast
paths live (the Super-agent property: the router is thin and unable
to work).

Extension point: a NEW supervision capability = a new EventClass here
(+ a priority row) and one worker subclass registered against it in
``workers.WORKER_FACTORIES`` — the picker's loop never changes.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict

from llm_agents.deep_agents.supervisor.event_queue import (
    EventKind, SupervisorEvent,
)


class EventClass(str, Enum):
    """What the PICKER should DO about an event — the dispatch key for
    the worker registry."""

    REPAIR = "repair"   # a lane window is open and the lane is paused
    REVIEW = "review"   # an observe-only diagnosis (zero veto)
    SWEEP = "sweep"     # the run settled — the final observe pass


# Dispatch priority when one drain yields several events: the paused
# lane's repair decision jumps ahead of everything (its grace window
# is the run's latency), belt reviews follow, the terminal sweep runs
# last.
CLASS_PRIORITY: Dict[EventClass, int] = {
    EventClass.REPAIR: 0,
    EventClass.REVIEW: 1,
    EventClass.SWEEP: 2,
}


def classify_event(event: SupervisorEvent) -> EventClass:
    """The event → worker-class mapping (pure, model-free)."""
    if event.kind is EventKind.NODE_FAILED:
        return EventClass.REPAIR
    if event.kind is EventKind.FAILURE_DETECTED:
        return EventClass.REVIEW
    return EventClass.SWEEP
