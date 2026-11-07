"""llm_agents.deep_agents.supervisor — the SUPERVISOR: an INDEPENDENT
per-run agent beside the thread graph, NOT a LangGraph node, NOT part
of the langgraph package (2026-11-05: a package of its own — engine /
loop / tools / prompts / watchdog; 2026-11-06: the REPAIR LANE —
repair.py + poller.py). The SUPERVISOR SESSION (poller.supervisor_
session) is the backend polling job the runner starts with every
graph: it watches the settled node states for the WHOLE run, and on ANY
root-path failure (biz step, wave member, review reviewer — the lane
pauses at the JOIN after every parallel before-node settled) the step
lane opens a BOUNDED PAUSE WINDOW (the RepairChannel) while the
session decides — code gates first, then ONE bounded tool-carrying
intervention — among THREE actions: retry-step (a transient failure
re-runs; a RATE-LIMIT class retry waits the throttle cooldown inside
the window first), ignore-step (the failed node settles skipped and
the run resumes PAST it), no-action (the failure stands). Idle on healthy runs (one cheap
DB poll per interval, zero LLM); the session's exit runs the final
observe sweep (SupervisorSession.sweep — the old post-run
tail, deduped against mid-run reviews).

Layering: imports planning (states, prompts, review persistence,
reopen_state) and tools (its kit's Tool classes) and core (provider,
request chain); imports langgraph ONLY under TYPE_CHECKING — the
runner injects the ``NodeRunWriter`` factory, so only langgraph
composes at runtime.
"""
from llm_agents.deep_agents.supervisor.engine import (
    NODE_SUPERVISOR, SUPERVISOR_REVIEW_CHARS, SUPERVISOR_SYSTEM_PROMPT,
    InterventionOutcome, intervene_on_failure, root_path_failures,
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
    "ACTION_IGNORE", "ACTION_NONE", "ACTION_RETRY", "NODE_SUPERVISOR",
    "SUPERVISOR_MAX_TOOL_ROUNDS", "SUPERVISOR_REVIEW_CHARS",
    "SUPERVISOR_SYSTEM_PROMPT", "InterventionOutcome", "RepairChannel",
    "BoundedToolLoop", "SupervisorSession", "SupervisorToolKit",
    "WriterFactory", "intervene_on_failure",
    "is_rate_limited", "is_transient", "parse_action",
    "root_path_failures", "should_retry",
    ]
