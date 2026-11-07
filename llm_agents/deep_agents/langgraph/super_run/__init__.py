"""llm_agents.deep_agents.langgraph.super_run — THE SUPER-RUN
ORCHESTRATOR (2026-11-06 three-graph refactor): one run = THREE
chained LangGraphs, one thread each, chained by ``super_thread_id``
(``llm.deep_agent_states.super_thread_id`` — the root thread's id
stamped on every row of the run):

    P  plan (type 0)    — ``build_plan_graph``: the PlanAgent's
                          batched proposal session → the complete plan
                          payload (the plan context row IS the graph's
                          final output — what the custom graph is
                          built from). The caller-supplied thread id;
                          its own id IS the chain id.
    C  custom (type 1)  — ``build_execution_graph`` over the plan's
                          steps (the on-demand graph the plan built;
                          waves, forks, compaction, the repair lane) →
                          the findings-synthesis output node (the
                          ``graph-output`` handoff row).
    R  result (type 2)  — ``build_execution_graph`` over the result
                          template's four REVIEW SEATS (return_estimate
                          / risk_analysis / optimistic_review /
                          pessimistic_review — one parallel wave) → the
                          final-answer output node (the
                          ``final-result`` row the UI's Result view
                          reads).

C and R are INDEPENDENT compiled graphs; R ALWAYS WAITS for C (the
orchestrator awaits C's ainvoke before minting R — the result stage
reads C's output node document as its input, seeded into its findings
bridge). Shared across all three: ONE wall-clock budget
(``run_timeout_sec`` from the single ``run_started`` anchor), the
prompt-ladder pass-down, ONE sandbox session, ONE supervisor session
(the repair channel + the backend poller watching the whole chain),
and the run-context side channel.

DEGRADED RUNS (the plan never persisted) stay entirely in P — the
plain execute lane, no C/R threads minted; the always-answer contract
is unchanged (``run_thread``'s pre-graph fail-soft shape). A stopped
run settles the whole chain (``settle_thread`` is chain-aware).

The orchestration lives in :class:`SuperGraphRunner`; ``run_thread``
is the thin public entry the server/CLI call (same signature as
before the refactor).

(PACKAGE NOTE, 2026-11-07: this was the single
``langgraph/super_run.py`` module; the split moved every
class/function body byte-identical into the submodules
below, and this package re-exports the same names.)
"""
from __future__ import annotations

from .chain import STOP_FAILURE, SuperGraphRunner, _suppress_cancel  # noqa: F401
from .entry import run_thread

__all__ = [
    "STOP_FAILURE", "SuperGraphRunner", "run_thread",
]


