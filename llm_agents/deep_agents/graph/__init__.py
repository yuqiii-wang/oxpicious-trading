"""llm_agents.deep_agents.graph — the ORCHESTRATION: the plan compiled
as a LangGraph node chain, and the thread's persistence surfaces.

One deep-agent run is one THREAD executed as a StateGraph whose node
chain IS the plan — the plan node studies the business input and writes
the research steps; each step then executes as its OWN node hop (its
own superstep, its own LLM conversation), routed hop by hop off the
living plan tree so replans steer the very next hop:

    START → plan → next? ─┬→ step → next? ─┬→ step → … → finalize → result → END
                          │(no plan)       │(no pending step)
                          └→ execute ──────┘ (the degraded one-conversation lane)

  * ``runner``      — the StateGraph (plan / step / compact /
    degraded-execute / finalize / result nodes), ``run_thread``, the
    per-hop engines (inline step conversation vs delegated
    ``run_delegation``), the findings accumulation and the
    checkpointer/store compilation;
  * ``prompts``     — the prompt surfaces: the planner's business
    framing, the dynamic-template machinery, and the per-step
    conversation assembly (directive + findings bridge, with the
    compacted digest block riding beside the raw tail);
  * ``compaction``  — CONTEXT COMPACTION: the context-limit estimator
    (when the step lane's accumulated evidence approaches the model
    window the router detours through the compact node) and the
    one-call engine that merges digest + raw findings into a fresh
    bounded digest — finished steps merge into the latest compacted
    node instead of aging away;
  * ``hooks``       — the thread-side delegation wiring (fork child
    states, context minting, the subagent plan kits) and the fork
    broker (update_plan's terminate op);
  * ``state_store`` — the deep-agent LangGraph BaseStore (runner-lane
    node states + the LLM-context registry over
    ``text.deep_agent_store_items``) and the NodeRunWriter that keeps
    ONE context per node execution.

Depends on ``core`` (the loop, delegation, sandbox) and ``planning``
(the authorities, the store) — nothing depends back on it except the
entrypoints (``server`` / ``cli``).
"""
from llm_agents.deep_agents.graph.runner import (
    ThreadRunResult, build_thread_graph, run_thread, settle_thread,
)

__all__ = ["ThreadRunResult", "build_thread_graph", "run_thread",
           "settle_thread"]
