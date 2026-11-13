"""llm_agents.deep_agents.langgraph — the ORCHESTRATION: the three
chained graphs a run compiles into, and the threads' persistence
surfaces.

One deep-agent run is a CHAIN of three LangGraphs (2026-11-06
three-graph refactor), one thread each, chained by
``llm.deep_agent_states.super_thread_id`` — the root (plan) thread's
id stamped on every row of the run:

    P plan   — build_plan_graph: the PlanAgent's proposal session →
               the complete plan payload (the plan context row is the
               graph's final output)
    C custom — build_execution_graph over the plan's steps (waves,
               spawns, compaction, the repair lane) → the findings
               synthesis (the graph-output handoff row)
    R result — build_result_graph over the saved type-result template's
               seats: ONE REAL LangGraph node per review seat, the four
               reviews as a TRUE parallel wave into the review_join
               barrier → the run's final answer (the final-result row);
               ALWAYS runs — full chain after C, degraded no-plan runs
               after the execute lane (2026-11-07)

    START ─┬→ step → next? ─┬→ step → … → finalize → output → END   (C)
           └→ (no plan) execute ──┘                                  (P)
    START ─┬→ seat ─┐                                                (R)
           ├→ seat ─┼→ review_join ─→ finalize → output → END
           └→ seat ─┘   (one REAL node per saved template seat;
                         retry verdicts route back to a seat)

  * ``runner``      — the graph builders (plan / step / compact /
    degraded-execute / finalize / output nodes), the per-hop engines
    (inline step conversation vs delegated ``run_delegation``), the
    findings accumulation and the store compilation;
  * ``super_run``   — THE ORCHESTRATOR: :class:`SuperGraphRunner`
    sequences the three graphs under one super_thread_id (one budget,
    one sandbox, one supervisor session) — ``run_thread``, the public
    entry the server/CLI call;
  * ``prompts``     — the prompt surfaces: the planner's business
    framing, the dynamic-template machinery, the per-step conversation
    assembly (directive + findings bridge, with the compacted digest
    block riding beside the raw tail) and the two output-node
    directives;
  * ``compaction``  — CONTEXT COMPACTION: the context-limit estimator
    (when the step lane's accumulated evidence approaches the model
    window the router detours through the compact node) and the
    one-call engine that merges digest + raw findings into a fresh
    bounded digest — finished steps merge into the latest compacted
    node instead of aging away;
  * ``hooks``       — the thread-side delegation wiring (spawn child
    states, context minting, the subagent plan kits) and the spawn
    broker (update_plan's terminate op);
  * ``state_store`` — the deep-agent LangGraph BaseStore (runner-lane
    node states + the LLM-context registry over
    ``llm.deep_agent_store_items``) and the NodeRunWriter that keeps
    ONE context per node execution.

Depends on ``core`` (the loop, delegation, sandbox) and ``planning``
(the authorities, the store) — nothing depends back on it except the
entrypoints (``server`` / ``cli``).
"""
from llm_agents.deep_agents.langgraph.runner import (
    CUSTOM_OUTPUT_SPEC, RESULT_OUTPUT_SPEC, GraphOutputSpec,
    ResultGraphState, ThreadRunResult, build_execution_graph,
    build_plan_graph, build_result_graph, settle_thread,
)
from llm_agents.deep_agents.langgraph.super_run import (
    SuperGraphRunner, run_thread,
)

__all__ = [
    "CUSTOM_OUTPUT_SPEC", "RESULT_OUTPUT_SPEC", "GraphOutputSpec",
    "ResultGraphState", "SuperGraphRunner", "ThreadRunResult",
    "build_execution_graph",
    "build_plan_graph", "build_result_graph", "run_thread", "settle_thread",
]

