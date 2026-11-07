"""llm_agents.deep_agents.langgraph.runner — the THREE-GRAPH CHAIN's
graph builders, as a package since 2026-11-07 (the former 1,827-line
runner.py; every class/function body moved byte-identical):

  * ``state.py``       — the graph state side: the ``ThreadState``
    TypedDict, the run context side channel, the output charters
    (``GraphOutputSpec`` + the custom/result specs) and the
    ``ThreadRunResult``; carries the STATE SERIALIZABILITY contract;
  * ``base.py``        — ``ExecutionGraphBuilderBase``: the ctor
    captures + the shared sandbox cell + the DYNAMIC prompt-loading
    rung (catalog rows + the raw-instruction cache);
  * ``step_lane.py``   — the ``StepLane`` mixin: the looping step lane
    (inline hops, delegated forks, the parallel wave);
  * ``nodes.py``       — the ``TailNodes`` mixin: compact / degraded
    execute / the FINAL OUTPUT NODE / finalize / routers / ``build``;
  * ``execution.py``   — the assembled
    ``class ExecutionGraphBuilder(...)`` + ``build_execution_graph``;
  * ``plan_builder.py``— ``PlanGraphBuilder`` + ``build_plan_graph``
    (the chain's first graph).
"""
from __future__ import annotations

from typing import List, Optional

from llm_agents.deep_agents.planning.store import (
    PLAN_STORE, StateRow,
)
from llm_agents.deep_agents.supervisor import (  # noqa: F401 (tests)
    SupervisorSession,
)

from .state import (
    CUSTOM_OUTPUT_SPEC, RESULT_OUTPUT_SPEC, GraphOutputSpec, ThreadRunResult,
    ThreadState, _RunContext,
)
from .base import ExecutionGraphBuilderBase
from .step_lane import StepLane
from .nodes import TailNodes
from .execution import ExecutionGraphBuilder, build_execution_graph
from .plan_builder import PlanGraphBuilder, build_plan_graph

__all__ = [
    "CUSTOM_OUTPUT_SPEC", "RESULT_OUTPUT_SPEC", "GraphOutputSpec",
    "ExecutionGraphBuilder", "ExecutionGraphBuilderBase", "PlanGraphBuilder",
    "StepLane", "TailNodes", "ThreadRunResult", "ThreadState",
    "build_execution_graph", "build_plan_graph", "settle_thread",
]


async def settle_thread(
    thread_id: str, *, failure: Optional[str] = None
) -> List[StateRow]:
    """Module-level delegate over :meth:`PLAN_STORE.settle_chain` — the
    exported name the langgraph package re-exports."""
    return await PLAN_STORE.settle_chain(thread_id, failure=failure)
