"""llm_agents.deep_agents.planning.session — the PLAN AGENT SESSION
(2026-11-06): the planner as a standalone agent OUTSIDE the LangGraph
graph. The plan needs no graph machinery — no node, no checkpoint, no
state round-trip: ``agents.plan_agent.PlanAgent`` runs
:class:`PlanSession` BEFORE the chain's graphs invoke (the way the
supervisor session runs BESIDE the graphs), the plan tree lives in the
plan store as always (``llm.deep_agent_states`` — never in graph
state), and the graph enters at the step lane (``plan_ok``) or the
degraded execute lane.

The plan agent itself is DEEP BUT NOT TOO COMPLEX: the batched
proposal engine (:class:`PlanSession` — the independent
:class:`planning.planner.Planner` session streaming validated batches,
each persisting NOW) plus the GRADUAL PROMPT-LOADING LADDER resolved
alongside it. The engine is a verbatim relocation of the runner's
former ``_propose_and_persist_plan``; spawning sub-agents from here
needs no structural change (a future engine may run a tool-carrying
conversation instead — the session's contract is the result type).

Layering: pure planning domain — imports planning.*, skills, limits;
no langgraph import at any binding (the runner injects the writer
factory and owns the lifecycle — the plan writer begins with
checkpoint None).

Package layout (the former single session.py, split 2026-11-07):
standing.py (AGENT_STANDING_SYSTEM + standing_system — the shared
opening prompt) / pass_down.py (PromptBudget, _catalog_mirror,
item_labels, ForkPassDown, plan_payload_steps — what the session hands
down to hops, forks and the payload) / session.py (PlanSession +
PlanSessionResult). This root re-exports the whole public surface —
including the private ``_catalog_mirror`` the live probe imports — so
every importer keeps importing from ``planning.session`` unchanged.
"""
from llm_agents.deep_agents.planning.session.pass_down import (
    ForkPassDown, PromptBudget, _catalog_mirror, item_labels,
    plan_payload_steps,
)
from llm_agents.deep_agents.planning.session.session import (
    PlanSession, PlanSessionResult,
)
from llm_agents.deep_agents.planning.session.standing import (
    AGENT_STANDING_SYSTEM, standing_system,
)

__all__ = [
    "PlanSession", "PlanSessionResult",
    "standing_system", "AGENT_STANDING_SYSTEM",
    "ForkPassDown", "PromptBudget", "item_labels", "plan_payload_steps",
]
