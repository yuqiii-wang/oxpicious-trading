"""llm_agents.deep_agents.langgraph.runner.review_lane — the RESULT
graph's REVIEW LANE (2026-11-07): the run's third graph compiled as a
REAL LangGraph over the saved type-result template's seats.

One node per seat (``return_estimate`` / ``risk_analysis`` /
``optimistic_review`` / ``pessimistic_review`` — or whatever the
DB-editable ``default-result-wave`` row carries), all four running as a
TRUE parallel wave in ONE LangGraph superstep — LangGraph's own
fan-out/fan-in replaces the former single looping ``step`` node whose
one hop gathered the wave inside itself:

    START ──→ ┌─ return_estimate ───┐
              ├─ risk_analysis ─────┤
              ├─ optimistic_review ─┼─→ review_join ─→ finalize → output → END
              └─ pessimistic_review ┘      │
                                           └─(retry verdicts)→ back to a seat

* the SEATS are the orchestrator's materialized rows (the saved
  langgraph template's review goals — ``planning.templates.
  fetch_result_template`` → ``materialize_result_steps``, fail-soft to
  ``plan_json.review_wave_steps``); the graph is literally "as saved
  from the langgraph template": every saved seat is a compiled node.
* each seat's body is the SAME inline hop the step lane runs
  (:class:`~llm_agents.deep_agents.agents.step_agent.StepAgent` over
  the shared :meth:`ExecutionGraphBuilderBase.build_step_deps`
  collaborators) — its writer keeps the ``step-<state_id>`` item name
  so the Thread Graph canvas joins it to its state row unchanged.
* seats report ONLY through the ``review_results`` channel (the ONE
  reducer-carrying key of :class:`~...runner.state.ResultGraphState` —
  parallel nodes may not write the plain keys); the ``review_join``
  barrier (LangGraph waits for ALL wave members, failed included) is
  its single writer: it folds the outcomes into the findings bridge in
  ORDINAL order whatever the completion order, adds the counters,
  picks the answer (the highest-ordinal completed seat — the wave's
  parity), and runs THE SUPERVISOR REPAIR WINDOW (the join pause,
  parity with ``step_lane``'s post-wave window): failed seats signal
  the repair channel, ``retry-step`` verdicts route BACK to the failed
  seat node (the poller re-opened the row; the verdict's instruction
  rides ``resume_instructions`` into ``StepAgent.resume``), ``ignore``/
  ``no-action`` proceed — reviewers never end the lane, the output
  seat notes them.
* the tail is the SHARED tail (``TailNodes``): finalize settles the
  thread, the output seat writes the run's final-result document.
* NO compact node: the wave's evidence bridge is already capped (the
  custom graph compacts before its handoff; the handoff seed + the
  seats' lines stay well inside the block budget).

Fail-soft as everywhere in the harness: the join never fails the lane
(an exception merges what it has and routes to finalize); a seat that
raised still reported its own failed outcome (StepAgent's contract),
and a dead-evidence run never enters the seats at all (the entry
router goes straight to finalize — the output records the failure).
"""
from __future__ import annotations

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore

from llm_agents.deep_agents.core.mcp import McpToolClient
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.agents import StepAgent
from llm_agents.deep_agents.langgraph.prompts import finding_line
from llm_agents.deep_agents.langgraph.runner.base import (
    ExecutionGraphBuilderBase,
)
from llm_agents.deep_agents.langgraph.runner.nodes import TailNodes
from llm_agents.deep_agents.langgraph.runner.state import (
    RESULT_OUTPUT_SPEC, GraphOutputSpec, ResultGraphState, ThreadState,
    _RunContext,
)
from llm_agents.deep_agents.langgraph.state_store import (
    NODE_REVIEW_JOIN, NodeRunWriter, entry_checkpoint_id,
)
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.store import StateRow
from llm_agents.deep_agents.supervisor.repair import (
    ACTION_IGNORE, ACTION_RETRY, RepairChannel,
)

logger = logging.getLogger("deep_agents.runner.review_lane")

# The review-join's IN-GRAPH retry-pass ceiling (belt over the poller's
# own per-node ``supervisor_max_node_retries`` budget — the poller
# stops re-opening rows past it, so a routed-back pass finds nothing to
# retry and the join proceeds; the ceiling only bounds a misbehaving
# channel).
REVIEW_JOIN_MAX_PASSES = 3


class ReviewLane:
    """The result graph's review nodes and join barrier — the mixin
    whose per-build captures (the seat rows under their uniquified node
    names, the shared plan-mutation lock, the wave-width semaphore) are
    initialized by :meth:`_init_review_lane` from the builder ctor."""

    def _init_review_lane(self, seats: List[StateRow]) -> None:
        """The per-build captures: uniquified seat node names (a
        hand-edited template could collide — ``add_node`` would raise;
        the first seat keeps its bare tag, later dupes gain ``_2`` /
        ``_3`` suffixes), the state_id → name map for the repair
        routing, ONE plan-mutation lock for the seats' ``update_plan``
        writes (the wave's plan_lock parity — all seats are inline) and
        the wave-width semaphore (``max_parallel_delegated_steps``,
        default 4 = the seeded wave's width)."""
        seen: Dict[str, int] = {}
        named: List[Tuple[str, StateRow]] = []
        for row in seats:
            base = (str(row.node or "").strip()
                    or f"review_{len(named) + 1}")
            n = seen.get(base, 0)
            seen[base] = n + 1
            named.append((base if n == 0 else f"{base}_{n + 1}", row))
        self._review_seats: List[Tuple[str, StateRow]] = named
        self._seat_name_by_state_id: Dict[int, str] = {
            row.state_id: name for name, row in named}
        self._review_plan_lock: asyncio.Lock = asyncio.Lock()
        self._review_slots: asyncio.Semaphore = asyncio.Semaphore(
            run_limits().max_parallel_delegated_steps)

    @staticmethod
    def _seat_outcome(
        seat_name: str,
        seat_row: StateRow,
        status: str,
        finding: str,
        n_llm: int,
        n_tool: int,
    ) -> Dict[str, Any]:
        """One seat's outcome dict — the ONLY thing a parallel seat
        writes (the reduced ``review_results`` channel; plain JSON-ish,
        the state-serializability contract)."""
        return {
            "node": seat_name,
            "state_id": int(seat_row.state_id),
            "ordinal": int(seat_row.ordinal),
            "title": str(seat_row.title or ""),
            "status": status,
            "finding": str(finding or ""),
            "n_llm": int(n_llm),
            "n_tool": int(n_tool),
        }

    def seat_node(self, seat_row: StateRow, seat_name: str) -> Callable:
        """One REVIEW SEAT as a real LangGraph node — the same inline
        hop the step lane runs (a fresh StepAgent conversation over the
        pre-join findings bridge, stats/kg/prompt kits, the shared
        sandbox cell), reporting only through ``review_results``. The
        instructed-resume contract rides unchanged: a supervisor retry
        verdict's entry in ``resume_instructions`` switches the agent
        into resume mode (the instruction + the prior attempt's error
        + its recorded-traffic pointer). Never raises — a failure is
        the seat's own failed outcome; CancelledError propagates (the
        orchestrator's stop path settles the chain)."""
        async def _run_seat(state: ResultGraphState) -> Dict[str, Any]:
            thread_id = state.get("thread_id") or ""
            # The run's TOTAL wall-clock budget (parity with the step
            # lane's per-hop check): past it the seat stands down with
            # NO outcome — the join merges what ran, the pending row
            # settles not-run at finalize.
            run_started = state.get("run_started")
            if (isinstance(run_started, (int, float))
                    and time.monotonic() - run_started
                    > run_limits().run_timeout_sec):
                logger.warning(
                    "review seat %s stood down — the run exceeded its "
                    "total budget", seat_name)
                return {}
            async with self._review_slots:
                deps = self.build_step_deps(
                    state, thread_id=thread_id,
                    findings=list(state.get("findings") or []))
                agent = StepAgent(
                    self._provider, deps=deps, thread_id=thread_id,
                    model=state.get("model"),
                    writer_factory=lambda: NodeRunWriter(
                        self._store, thread_id, f"step-{seat_row.state_id}",
                        model=state.get("model"), llm=True,
                        state_id=seat_row.state_id))
                info = (state.get("resume_instructions") or {}).get(
                    str(seat_row.state_id))
                if info:
                    status, finding, n_llm, n_tool = await agent.resume(
                        seat_row,
                        instruction=str(info.get("instruction") or ""),
                        prior_error=str(info.get("prior_error") or ""),
                        prior_context_id=(
                            seat_row.llm_context_id or None))
                else:
                    status, finding, n_llm, n_tool = await agent.run(
                        seat_row, plan_lock=self._review_plan_lock)
            return {"review_results": [self._seat_outcome(
                seat_name, seat_row, status, finding, n_llm, n_tool)]}
        return _run_seat

    async def review_join(self, state: ResultGraphState) -> Dict[str, Any]:
        """The wave's FAN-IN BARRIER — LangGraph holds it until every
        seat of the superstep settles (failed members included, exactly
        the parallel-join semantics the step lane's gather provided).
        Single writer: folds the latest outcome per seat into the
        findings bridge in ordinal order, adds the counters, picks the
        answer, then runs the supervisor repair window over the failed
        seats (retry → route back to those seat nodes; ignore /
        no-action → proceed). Reviewers never end the lane."""
        thread_id = state.get("thread_id") or ""
        writer = NodeRunWriter(
            self._store, thread_id, NODE_REVIEW_JOIN, llm=False)
        await writer.begin(entry_checkpoint_id())
        update: Dict[str, Any] = {}
        try:
            outcomes = [o for o in (state.get("review_results") or [])
                        if isinstance(o, dict)]
            # The LATEST outcome per seat (later appends are later
            # passes — a retried seat's fresh result supersedes its
            # failed one), then ordinal order for the bridge.
            latest: Dict[int, Dict[str, Any]] = {}
            for o in outcomes:
                latest[int(o.get("state_id") or 0)] = o
            seats = sorted(
                latest.values(),
                key=lambda o: (int(o.get("ordinal") or 0),
                               int(o.get("state_id") or 0)))
            findings = list(state.get("findings") or [])
            n_llm = int(state.get("steps") or 0)
            n_tool = int(state.get("n_tool_calls") or 0)
            answer_finding: Optional[str] = None
            failed: List[Dict[str, Any]] = []
            for o in seats:
                findings.append(finding_line(
                    int(o.get("ordinal") or 0),
                    str(o.get("title") or o.get("node") or ""),
                    str(o.get("finding") or "")))
                n_llm += int(o.get("n_llm") or 0)
                n_tool += int(o.get("n_tool") or 0)
                if o.get("status") == "completed":
                    answer_finding = str(o.get("finding") or "")
                else:
                    failed.append(o)
            update.update({
                "findings": findings,
                "steps": n_llm,
                "n_tool_calls": n_tool,
                # The consumed resume entries: this pass's seats
                # settled (resumed to completed/failed) — their
                # instructions drop, exactly the step lane's
                # carry-forward.
                "resume_instructions": {
                    k: v for k, v in (
                        state.get("resume_instructions") or {}).items()
                    if int(k) not in latest},
            })
            if answer_finding:
                update["answer"] = " ".join(answer_finding.split())
            # THE SUPERVISOR REPAIR WINDOW — the join pause. The run's
            # total budget past its deadline never re-runs seats (the
            # pending rows settle at finalize); grace 0 / no channel /
            # no-action verdicts leave the failures standing and the
            # lane proceeds — the output seat notes them, the poller's
            # exit sweep reviews them.
            retry_seats: List[str] = []
            run_started = state.get("run_started")
            budget_spent = (
                isinstance(run_started, (int, float))
                and time.monotonic() - run_started
                > run_limits().run_timeout_sec)
            if (failed and self._repair is not None and thread_id
                    and not budget_spent
                    and int(state.get("review_passes") or 0)
                    < REVIEW_JOIN_MAX_PASSES):
                try:
                    failed_err = {
                        int(o.get("state_id") or 0):
                            str(o.get("finding") or "")[:300]
                        for o in failed}
                    await self._repair.signal_failures([
                        (sid, err) for sid, err in failed_err.items()])
                    verdicts = await self._repair.await_verdicts(
                        grace_sec=run_limits().supervisor_repair_grace_sec)
                    retried = {
                        sid: v for sid, v in verdicts.items()
                        if v.action == ACTION_RETRY}
                    ignored = {
                        sid for sid, v in verdicts.items()
                        if v.action == ACTION_IGNORE}
                    resolved = set(retried) | set(ignored)
                    if resolved:
                        logger.info(
                            "review join repair: re-running %s, "
                            "ignoring %s", sorted(retried), sorted(ignored))
                        resume = dict(update["resume_instructions"])
                        for sid, verdict in retried.items():
                            name = self._seat_name_by_state_id.get(int(sid))
                            if name is not None:
                                retry_seats.append(name)
                            resume[str(sid)] = {
                                "instruction": verdict.instruction,
                                "prior_error": failed_err.get(int(sid), ""),
                            }
                        update["resume_instructions"] = resume
                        update["review_passes"] = (
                            int(state.get("review_passes") or 0) + 1)
                except Exception as exc:  # noqa: BLE001 — the window is
                    logger.warning(  # a lane-shortcut, never the lane
                        "review join repair window failed: %s", exc)
            update["retry_seats"] = retry_seats
            await writer.finish("completed", n_states=len(seats))
        except Exception as exc:  # noqa: BLE001 — the join never fails
            logger.warning("review join failed: %s", exc)
            await writer.finish("failed",
                                error=f"{type(exc).__name__}: {exc}")
            update = {"retry_seats": []}
        return update

    # -- the routers -----------------------------------------------------

    def route_result_entry(self, state: ResultGraphState) -> List[str]:
        """The result graph's entry: evidence (the orchestrator's flag —
        the custom/degraded lane's answer or findings) fans out to EVERY
        seat node (the parallel wave, one LangGraph superstep); a
        dead-evidence run routes straight to finalize — the output seat
        records the failure document, no wave is spent."""
        if state.get("plan_ok", False) and self._review_seats:
            return [name for name, _row in self._review_seats]
        return ["finalize"]

    def route_after_join(self, state: ResultGraphState) -> List[str]:
        """After the barrier: the repair verdicts' retried seats re-run
        (their fresh outcomes reach the next join pass), everything else
        finalizes."""
        retried = [name for name in (state.get("retry_seats") or [])
                   if name in self._seat_name_by_state_id.values()]
        return retried or ["finalize"]


class ResultGraphBuilder(ExecutionGraphBuilderBase, ReviewLane, TailNodes):
    """THE RESULT GRAPH's builder (2026-11-07 — the former
    ``build_execution_graph(degraded_lane=False)`` reuse): one REAL
    LangGraph node per saved type-result template seat + the
    ``review_join`` barrier + the shared finalize/output tail. The
    checkpointer stays gone (accepted as None for call-site
    compatibility); *seats* are the orchestrator's persisted rows
    (``PLAN_STORE.create_thread_states`` — fixed for the run: the wave
    never replans, the review seats are the graph's literal
    definition)."""

    def __init__(
        self,
        provider: ZhipuChatProvider,
        mcp: McpToolClient,
        run_ctx: _RunContext,
        *,
        checkpointer: Any = None,
        store: Optional[BaseStore] = None,
        repair: Optional[RepairChannel] = None,
        sandbox_cell: Optional[List[Any]] = None,
        output: GraphOutputSpec = RESULT_OUTPUT_SPEC,
        seats: Optional[List[StateRow]] = None,
        decision_engine: Optional[Any] = None,
    ) -> None:
        super().__init__(
            provider, mcp, run_ctx, checkpointer=checkpointer,
            store=store, repair=repair, sandbox_cell=sandbox_cell,
            output=output, degraded_lane=False,
            decision_engine=decision_engine)
        self._init_review_lane(list(seats or []))

    def build(self):
        builder = StateGraph(ResultGraphState)
        for name, seat in self._review_seats:
            builder.add_node(name, self.seat_node(seat, name))
        builder.add_node(NODE_REVIEW_JOIN, self.review_join)
        builder.add_node("finalize", self.finalize_node)
        builder.add_node("output", self.output_node)
        builder.add_conditional_edges(
            START, self.route_result_entry,
            {**{name: name for name, _ in self._review_seats},
             "finalize": "finalize"})
        for name, _ in self._review_seats:
            builder.add_edge(name, NODE_REVIEW_JOIN)
        builder.add_conditional_edges(
            NODE_REVIEW_JOIN, self.route_after_join,
            {**{name: name for name, _ in self._review_seats},
             "finalize": "finalize"})
        builder.add_edge("finalize", "output")
        builder.add_edge("output", END)
        return builder.compile(checkpointer=self._checkpointer,
                               store=self._store)


def build_result_graph(
    provider: ZhipuChatProvider,
    mcp: McpToolClient,
    run_ctx: _RunContext,
    *,
    checkpointer: Any = None,
    store: Optional[BaseStore] = None,
    repair: Optional[RepairChannel] = None,
    sandbox_cell: Optional[List[Any]] = None,
    output: GraphOutputSpec = RESULT_OUTPUT_SPEC,
    seats: Optional[List[StateRow]] = None,
    decision_engine: Optional[Any] = None,
):
    """Compile THE RESULT GRAPH — :class:`ResultGraphBuilder`'s
    :meth:`build` (the four+ saved-template review seats as one real
    parallel wave → review_join → finalize → output)."""
    return ResultGraphBuilder(
        provider, mcp, run_ctx, checkpointer=checkpointer, store=store,
        repair=repair, sandbox_cell=sandbox_cell, output=output,
        seats=seats,
        decision_engine=decision_engine).build()
