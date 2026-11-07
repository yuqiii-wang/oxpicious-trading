"""llm_agents.deep_agents.langgraph.super_run.chain — ``SuperGraphRunner`` — the ORCHESTRATOR: one run =
three chained graphs (plan → custom → result, one thread each)
sequenced under one ``super_thread_id``, one sandbox cell, one
supervisor session, one wall-clock budget.
"""
from __future__ import annotations

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from langgraph.store.base import BaseStore

from llm_agents.deep_agents.core.mcp import McpToolClient
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.core.loop import AgentResult
from llm_agents.deep_agents.langgraph.runner import (
    CUSTOM_OUTPUT_SPEC, RESULT_OUTPUT_SPEC, _RunContext,
    build_execution_graph, build_plan_graph, ThreadRunResult,
)
from llm_agents.deep_agents.langgraph.prompts import finding_line
from llm_agents.deep_agents.langgraph.state_store import (
    NODE_PLAN, NODE_SUPERVISOR, DeepAgentStore, NodeRunWriter,
    settle_node_items,
)
from llm_agents.deep_agents.agents import PlanAgent, SupervisorAgent
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.plan_json import review_wave_steps
from llm_agents.deep_agents.planning.session import (
    PlanSessionResult, plan_payload_steps,
)
from llm_agents.deep_agents.planning.store import (StateRow, identities_from_scope, PLAN_STORE)
from llm_agents.deep_agents.planning.templates import (
    TEMPLATE_LIBRARY,
)
from llm_agents.deep_agents.supervisor.repair import RepairChannel

logger = logging.getLogger("deep_agents.langgraph.super_run")


STOP_FAILURE = "stopped by user (POST /stop)"


class SuperGraphRunner:
    """One super run's orchestration: the three graphs in chain order
    under one ``super_thread_id`` (see the module docstring). Holds the
    run's shared pieces (store, repair channel, sandbox cell, supervisor
    task, the minted chain threads) and the fail-soft settles for every
    exit path."""

    def __init__(
        self,
        provider: ZhipuChatProvider,
        mcp: McpToolClient,
        *,
        goal: str,
        data: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        max_steps: Optional[int] = None,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        enable_sandbox: bool = True,
        thread_id: Optional[str] = None,
        plan_template_id: Optional[int] = None,
    ) -> None:
        self._provider = provider
        self._mcp = mcp
        self._goal = goal
        self._data = data
        self._model = model
        self._system = system
        self._temperature = temperature
        self._enable_sandbox = enable_sandbox
        self._plan_template_id = plan_template_id
        # The chain's ROOT thread (P) — caller-supplied when the caller
        # needs it before the run ends (the server registers the run
        # under it so /stop can cancel from the first await); None picks
        # a fresh uuid-hex.
        self._thread_id = thread_id or uuid.uuid4().hex
        # The chain id (= P): stamped on every row of P, C and R.
        self.super_thread_id = self._thread_id
        # The per-hop conversation budget (caller's explicit argument
        # wins, else the static deep_agent_config).
        self._max_steps = max(1, int(
            max_steps if max_steps is not None
            else run_limits().max_conversation_steps))
        # The chain's minted threads in order (P first) — the cancel /
        # engine-failure settles walk it.
        self._chain_threads: List[str] = [self._thread_id]

    # -- the run ---------------------------------------------------------
    async def run(self) -> ThreadRunResult:
        thread_id = self._thread_id
        run_ctx = _RunContext()
        prompt_keys = [
            key for key in ((self._data or {}).get("prompts") or [])
            if isinstance(key, str) and key
        ]
        # THE RUNNING PLANNING NODE (P's ordinal-0 'plan' row, stamped
        # with the chain id): the run's record exists from THIS moment
        # — the summary history's row and a non-404 thread read while
        # the plan LLM call is still thinking. The plan graph closes
        # the row when the proposal lands. Fail-soft like every store
        # call.
        plan_state_id: Optional[int] = None
        try:
            plan_state_id = await PLAN_STORE.begin_thread(
                thread_id, super_thread_id=self.super_thread_id)
        except Exception as exc:  # noqa: BLE001 — run starts unrecorded
            logger.warning("planning-node insert failed (run starts "
                           "unrecorded): %s", exc)
        # The scope picks link to the ROOT thread at run start
        # (llm.deep_agent_identities — the summary history's per-code
        # read resolves the chain through it); the standing system
        # prompt and the agent/tool prompt rows resolve here too, all
        # fail-soft (bookkeeping and prompt sourcing never fail a run).
        for identity in identities_from_scope(self._data):
            try:
                await PLAN_STORE.record_thread_identity(thread_id, identity)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning("thread identity persist failed: %s", exc)
        system = self._system
        if system is None:
            try:
                system = await PLAN_STORE.fetch_system_prompt()
            except Exception as exc:  # noqa: BLE001 — default stands
                logger.warning("system prompt fetch failed, using the "
                               "built-in default: %s", exc)
        try:
            agent_prompts = await PLAN_STORE.fetch_agent_prompts()
        except Exception as exc:  # noqa: BLE001 — constants cover it
            logger.warning("agent prompt fetch failed, using the built-in "
                           "constants: %s", exc)
            agent_prompts = {}
        try:
            tool_prompts = await PLAN_STORE.fetch_tool_prompts()
        except Exception as exc:  # noqa: BLE001 — constants cover it
            logger.warning("tool prompt fetch failed, using the built-in "
                           "descriptions: %s", exc)
            tool_prompts = {}

        supervisor_task: Optional[asyncio.Task] = None
        # No checkpointer (the 2026-11-06 contract): every graph
        # compiles stateless; the plan trees in the plan store are the
        # only source of truth between hops, and the supervisor session
        # watches the node states from the DB, not checkpoints.
        store: BaseStore = DeepAgentStore()
        # ONE repair channel + ONE supervisor session for the WHOLE
        # CHAIN: the supervisor is bound to the SUPER thread id (the
        # poller watches every thread's node states through the
        # super-scoped read and keys its reviews there); its exit runs
        # the final sweep.
        repair: RepairChannel = RepairChannel()
        # ONE sandbox session per RUN shared by every graph's hops.
        sandbox_cell: List[Any] = [None]
        # The run's TOTAL wall-clock anchor — ONE budget across all
        # three graphs (each graph's step lane checks it every hop).
        run_started = time.monotonic()
        try:
            supervisor_agent = SupervisorAgent(
                # THE SUPERVISOR IS BOUND TO THE SUPER THREAD ID — never
                # a segment langgraph thread: the chain id keys its
                # marker, its reviews and its kit scope (the poller and
                # watchdog resolve every keyed write through
                # super_thread_id; today the root P thread's id IS the
                # chain id, but the binding must not DEPEND on that).
                self._provider.chat_complete,
                thread_id=self.super_thread_id,
                model=self._model,
                writer_factory=lambda llm=False: NodeRunWriter(
                    store, self.super_thread_id, NODE_SUPERVISOR,
                    model=self._model, llm=llm))
            supervisor_task = asyncio.create_task(
                supervisor_agent.session(
                    self._provider, store, goal=self._goal, channel=repair,
                    sweep_writer_factory=lambda: NodeRunWriter(
                        store, self.super_thread_id, NODE_SUPERVISOR,
                        model=self._model),
                    model=self._model,
                    super_thread_id=self.super_thread_id))

            # -- GRAPH 1 — THE PLAN GRAPH (thread P) --------------------
            plan_agent = PlanAgent(
                self._provider.chat_complete, thread_id=thread_id,
                model=self._model,
                writer_factory=lambda: NodeRunWriter(
                    store, thread_id, NODE_PLAN, model=self._model))
            plan_graph = build_plan_graph(
                self._provider, self._mcp, run_ctx, store=store,
                plan_agent=plan_agent, plan_state_id=plan_state_id,
                plan_template_id=self._plan_template_id,
                prompt_keys=prompt_keys, agent_prompts=agent_prompts)
            config = {"recursion_limit": run_limits().recursion_limit}
            await plan_graph.ainvoke(
                {"goal": self._goal, "data": self._data,
                 "prompt_keys": prompt_keys,
                 "agent_prompts": agent_prompts,
                 "tool_prompts": tool_prompts,
                 "model": self._model, "max_steps": self._max_steps,
                 "system": system, "temperature": self._temperature,
                 "sandbox_enabled": self._enable_sandbox},
                config)
            plan_result = run_ctx.plan_result or PlanSessionResult(
                thread_id=thread_id, plan_ok=False)

            # -- GRAPH 2 — THE CUSTOM GRAPH (thread C; the degraded
            # lane stays in P on a failed plan) -------------------------
            custom_thread_id = (plan_result.custom_thread_id
                                if plan_result.plan_ok else None)
            graph = build_execution_graph(
                self._provider, self._mcp, run_ctx, store=store,
                repair=repair, sandbox_cell=sandbox_cell,
                output=CUSTOM_OUTPUT_SPEC, degraded_lane=True)
            graph_config: Dict[str, Any] = {
                "recursion_limit": run_limits().recursion_limit}
            if custom_thread_id:
                self._chain_threads.append(custom_thread_id)
                graph_config["configurable"] = {
                    "thread_id": custom_thread_id}
            try:
                values: Dict[str, Any] = await graph.ainvoke(
                    {**self._custom_initial_state(
                        plan_result, system=system,
                        agent_prompts=agent_prompts,
                        tool_prompts=tool_prompts,
                        run_started=run_started)},
                    graph_config)
            except asyncio.CancelledError:
                await self._settle_chain_items()
                raise

            # -- GRAPH 3 — THE RESULT GRAPH (thread R; ALWAYS after C) --
            if plan_result.plan_ok:
                values = await self._run_result_graph(
                    run_ctx=run_ctx, store=store, repair=repair,
                    sandbox_cell=sandbox_cell,
                    plan_result=plan_result, values_c=values,
                    system=system, agent_prompts=agent_prompts,
                    tool_prompts=tool_prompts,
                    run_started=run_started)

            # -- THE SUPERVISOR SESSION'S EXIT (the final sweep) --------
            try:
                repair.close()
                await asyncio.wait_for(supervisor_task, timeout=180)
            except asyncio.TimeoutError:
                supervisor_task.cancel()
                with _suppress_cancel():
                    await supervisor_task
                logger.warning("supervisor final sweep timed out")
            except asyncio.CancelledError:
                supervisor_task.cancel()
                with _suppress_cancel():
                    await supervisor_task
                await self._settle_chain_items()
                raise
            except Exception:  # noqa: BLE001 — belt; session self-guards
                logger.warning("supervisor session exit failed",
                               exc_info=True)
                supervisor_task.cancel()
                with _suppress_cancel():
                    await supervisor_task
        except asyncio.CancelledError:
            # The stop path (the server's /stop cancels the run task):
            # the graphs' finalize never runs — the caller's stop
            # handler settles the chain's STATE rows (settle_thread is
            # chain-aware); here the runner-lane node items close while
            # the store is still in scope. The planning row on a
            # mid-plan stop stays in_progress on purpose (the stop
            # settle closes it as 'stopped by user').
            raise
        except BaseException:
            # A graph ENGINE failed — nothing will ever settle the
            # running planning node row, and a row stuck in_progress
            # reads as a live run forever. Close it fail-soft, settle
            # the chain's node items, then let the original failure
            # propagate (the pre-begin_thread contract).
            with _suppress_cancel():
                await self._settle_chain_items()
            with _suppress_cancel():
                await PLAN_STORE.settle_plan_state(
                    thread_id, "failed",
                    error="run failed before its plan node settled")
            raise
        finally:
            # The supervisor session task must never outlive the run:
            # the normal path already awaited it (the sweep is done);
            # every other exit cancels it here — a poller left running
            # would leak LLM calls and DB reads into a dead thread.
            if supervisor_task is not None and not supervisor_task.done():
                supervisor_task.cancel()
                with _suppress_cancel():
                    await supervisor_task

        # The execute failure (if any) settles in finalize first; the
        # ORIGINAL exception then propagates to the caller.
        if run_ctx.error is not None:
            raise run_ctx.error
        values = values or {}
        if run_ctx.result is not None:
            result = run_ctx.result
        else:
            result = AgentResult(
                answer=str(values.get("answer") or ""),
                steps=int(values.get("steps") or 0),
                tool_calls=[],
            )
        # The settled CHAIN snapshot — every thread's rows in chain
        # order, re-read from the store fail-soft (the response's plan).
        plan_rows: List[StateRow] = []
        try:
            plan_rows = await PLAN_STORE.list_super_states(self.super_thread_id)
        except Exception as exc:  # noqa: BLE001 — result shape, not data
            logger.warning("settled chain re-read failed: %s", exc)
        return ThreadRunResult(
            result=result,
            thread_id=thread_id,
            super_thread_id=self.super_thread_id,
            plan=plan_rows,
            n_tool_calls=(len(run_ctx.result.tool_calls)
                          if run_ctx.result is not None
                          else int(values.get("n_tool_calls") or 0)),
        )

    # -- the graphs' shared initial states -------------------------------
    def _custom_initial_state(
        self,
        plan_result: PlanSessionResult,
        *,
        system: Optional[str],
        agent_prompts: Dict[str, str],
        tool_prompts: Dict[str, str],
        run_started: float,
    ) -> Dict[str, Any]:
        """The CUSTOM graph's initial state (or the degraded lane's —
        the same dict with a falsy thread id and plan_ok False): the
        plan session's outcome (the ladder pass-down, the entry flag,
        the persisted working thread id) beside the run constants and
        the SHARED wall-clock anchor."""
        return {
            "goal": self._goal,
            "data": self._data,
            "prompt_keys": [
                key for key in ((self._data or {}).get("prompts") or [])
                if isinstance(key, str) and key],
            "agent_prompts": agent_prompts,
            "tool_prompts": tool_prompts,
            "model": self._model,
            "max_steps": self._max_steps,
            "system": system,
            "temperature": self._temperature,
            "sandbox_enabled": self._enable_sandbox,
            # The plan session's outcome rides the INITIAL state: the
            # custom thread (None on the no-plan degrade — later
            # writers self-disable), the ladder pass-down + catalog,
            # and the entry flag the START router reads.
            "thread_id": plan_result.custom_thread_id,
            "prompt_texts": plan_result.prompt_texts,
            "prompt_catalog": plan_result.prompt_catalog,
            "plan_ok": plan_result.plan_ok,
            "run_started": run_started,
        }

    async def _run_result_graph(
        self,
        *,
        run_ctx: _RunContext,
        store: BaseStore,
        repair: RepairChannel,
        sandbox_cell: List[Any],
        plan_result: PlanSessionResult,
        values_c: Dict[str, Any],
        system: Optional[str],
        agent_prompts: Dict[str, str],
        tool_prompts: Dict[str, str],
        run_started: float,
    ) -> Dict[str, Any]:
        """GRAPH 3 — the RESULT graph (thread R), ALWAYS run after the
        custom graph completes: its four REVIEW SEATS come from the
        type-result template row (fail-soft to the built-in wave
        constants), its findings bridge is SEEDED with the custom
        graph's output-node synthesis (the handoff document the reviews
        read first), and its own output node writes the run's
        final-result document. A dead custom graph (no answer, no
        findings) skips the wave — R routes straight to finalize and
        its output node records the failure. Fail-soft: a persistence
        failure before the wave means no result graph — the run still
        answers through the orchestrator's return (the old
        never-block contract)."""
        seats = review_wave_steps()
        template_name = "builtin-wave"
        try:
            template = await TEMPLATE_LIBRARY.fetch_result_template()
            if template is not None:
                seats = TEMPLATE_LIBRARY.materialize_result_steps(
                    template.plan_json)
                template_name = template.name
        except Exception as exc:  # noqa: BLE001 — builtin wave stands
            logger.warning("result template unusable (%s) — the built-in "
                           "review wave runs", exc)
        result_thread_id = uuid.uuid4().hex
        answer_c = str(values_c.get("answer") or "")
        findings_c = [f for f in (values_c.get("findings") or []) if f]
        run_error_c = values_c.get("run_error")
        try:
            await PLAN_STORE.record_plan_context(
                result_thread_id,
                {"goal": self._goal, "graph": "result",
                 "super_thread_id": self.super_thread_id,
                 "template": {"name": template_name},
                 "steps": plan_payload_steps(seats)})
            await PLAN_STORE.create_thread_states(
                result_thread_id, seats,
                super_thread_id=self.super_thread_id)
        except Exception as exc:  # noqa: BLE001 — never fail the answer
            logger.warning("result graph persistence failed (%s) — the "
                           "run answers on the custom graph's output",
                           exc)
            return values_c
        self._chain_threads.append(result_thread_id)
        # The HANDOFF SEED: the custom graph's synthesis rides the
        # findings bridge as the reviews' first line (ordinal 0), the
        # raw findings after it; the digest (if C compacted) carries
        # over so the reviews read the whole evidence lane.
        seeded: List[str] = (
            [finding_line(0, "Custom graph output", answer_c)]
            if answer_c else []) + findings_c
        graph = build_execution_graph(
            self._provider, self._mcp, run_ctx, store=store,
            repair=repair, sandbox_cell=sandbox_cell,
            output=RESULT_OUTPUT_SPEC, degraded_lane=False)
        config = {
            "recursion_limit": run_limits().recursion_limit,
            "configurable": {"thread_id": result_thread_id},
        }
        try:
            return await graph.ainvoke(
                {**self._custom_initial_state(
                    plan_result, system=system,
                    agent_prompts=agent_prompts,
                    tool_prompts=tool_prompts,
                    run_started=run_started),
                 # R's own working thread + entry: the wave runs
                 # whenever there is anything to review (a dead custom
                 # graph routes straight to finalize — the output node
                 # records the failure document).
                 "thread_id": result_thread_id,
                 "plan_ok": bool(answer_c or findings_c),
                 # The handoff: C's evidence, counts and error carry in
                 # (answer first — R's output node is the final word,
                 # but a skipped/failed lane still answers with C's
                 # synthesis).
                 "findings": seeded,
                 "findings_digest": values_c.get("findings_digest"),
                 "n_compacted": int(values_c.get("n_compacted") or 0),
                 "answer": answer_c,
                 "steps": int(values_c.get("steps") or 0),
                 "n_tool_calls": int(values_c.get("n_tool_calls") or 0),
                 "run_error": run_error_c,
                 },
                config)
        except asyncio.CancelledError:
            await self._settle_chain_items()
            raise

    async def _settle_chain_items(self) -> None:
        """Close every chain thread's still-running node items — the
        cancel/unwind path (the STATE rows settle in the caller's stop
        handler through the chain-aware ``settle_thread``). One call
        per thread, fail-soft each."""
        for tid in list(self._chain_threads):
            try:
                await settle_node_items(None, tid, failure=STOP_FAILURE)
            except Exception as exc:  # noqa: BLE001 — best-effort close
                logger.warning("chain node-item settle failed (%s): %s",
                               tid, exc)


class _suppress_cancel:
    """``contextlib.suppress(Exception)`` that also swallows
    CancelledError (the unwind paths must not shadow the original
    cancel) — the old inline ``contextlib.suppress`` calls' exact
    semantics, named."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(
            exc_type, (Exception, asyncio.CancelledError))

