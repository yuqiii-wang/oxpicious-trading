"""llm_agents.deep_agents.supervisor — the SUPERVISOR: an INDEPENDENT
per-run agent beside the thread graph, NOT a LangGraph node, NOT part
of the langgraph package. Since 2026-11-11 an EVENT-QUEUE PICKER:
every notable run event lands in a coalescing queue
(``event_queue.py`` — lane window signals, belt-scan discoveries, the
runner's close), the session (``poller.py``) drains it, classifies
each event in pure code (``classify.py``) and launches the matching
worker sub-agent from the dispatch registry (``workers.py`` —
Repair / Observe / Sweep; a new capability is one EventClass + one
subclass + one registry entry). The step lane still opens a BOUNDED
PAUSE WINDOW at the join (``repair.py``'s RepairChannel — its two lane
calls are unchanged) while the RepairWorker decides among THREE
actions: retry-step (a transient failure re-runs; a RATE-LIMIT class
retry waits the throttle cooldown inside the window first),
ignore-step (the failed node settles skipped and the run resumes PAST
it), no-action (the failure stands). Idle on healthy runs (one cheap
DB poll per interval, zero LLM); the terminal RUN_SETTLED event
dispatches the final observe sweep — the old post-run tail, absorbed
into the queue.

Layering: imports planning (states, prompts, review persistence,
reopen_state) and tools (its kit's Tool classes) and core (provider,
request chain); imports langgraph ONLY under TYPE_CHECKING — the
runner injects the ``NodeRunWriter`` factory, so only langgraph
composes at runtime. ``event_queue`` is the bottom of the package
(imports nothing from it); ``repair`` → ``classify`` / ``workers`` →
``poller`` compose upward.
"""
from llm_agents.deep_agents.supervisor.engine import (
    NODE_SUPERVISOR, SUPERVISOR_REVIEW_CHARS, SUPERVISOR_SYSTEM_PROMPT,
    InterventionOutcome, intervene_on_failure, root_path_failures,
)
from llm_agents.deep_agents.supervisor.event_queue import (
    EventKind, FailureRef, RepairWindow, SupervisorEvent,
    SupervisorEventQueue,
)
from llm_agents.deep_agents.supervisor.classify import (
    CLASS_PRIORITY, EventClass, classify_event,
)
from llm_agents.deep_agents.supervisor.workers import (
    ObserveWorker, RepairWorker, SweepWorker, SupervisorWorker,
    WORKER_FACTORIES, WorkerOutcome,
)
from llm_agents.deep_agents.supervisor.loop import (
    SUPERVISOR_MAX_TOOL_ROUNDS, BoundedToolLoop,
)
from llm_agents.deep_agents.supervisor.poller import SupervisorSession
from llm_agents.deep_agents.supervisor.repair import (
    ACTION_IGNORE, ACTION_NONE, ACTION_RETRY, RepairChannel,
    is_rate_limited, is_transient, parse_action, should_retry,
)
from llm_agents.deep_agents.supervisor.tools import SupervisorToolKit
from llm_agents.deep_agents.supervisor.poller import WriterFactory

__all__ = [
    "ACTION_IGNORE", "ACTION_NONE", "ACTION_RETRY", "CLASS_PRIORITY",
    "EventClass", "EventKind", "FailureRef", "InterventionOutcome",
    "NODE_SUPERVISOR", "ObserveWorker", "RepairChannel", "RepairWorker",
    "RepairWindow", "SUPERVISOR_MAX_TOOL_ROUNDS", "SUPERVISOR_REVIEW_CHARS",
    "SUPERVISOR_SYSTEM_PROMPT", "SweepWorker", "SupervisorEvent",
    "SupervisorEventQueue", "SupervisorSession", "SupervisorToolKit",
    "SupervisorWorker", "WriterFactory", "WORKER_FACTORIES",
    "WorkerOutcome", "BoundedToolLoop", "classify_event",
    "intervene_on_failure", "is_rate_limited", "is_transient",
    "parse_action", "root_path_failures", "should_retry",
    ]
