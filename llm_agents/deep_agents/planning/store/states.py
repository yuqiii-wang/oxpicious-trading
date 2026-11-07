"""llm_agents.deep_agents.planning.store.states — the PLAN/STEP
LIFECYCLE WRITES (the StateStoreMixin).

BESIDE the initial open, the plan is MUTABLE at runtime
(``tools/plan_tools/`` holds the model-facing tools over the
permission model, ``authorities.PlanAuthority``): ``add_plan_states``
appends pending steps mid-run (main-lane or under a fork),
``revise_pending_state`` rewrites a not-yet-run step,
``skip_pending_states`` drops pending steps (audit-preserving), and
``fetch_state`` / ``list_thread_states`` read the CURRENT tree the
tick/update handlers resolve against. The MAIN thread owns pending
top-level nodes and may terminate running forks; a subagent owns only
the nodes it minted under its own forked path.

Every write announces a deep-agent event (``events.publish`` — the
daemon's /ws WebSocket channel); the publishes are fire-and-forget
announcements beside the write, never a part of its contract.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from _common.sql_writes import render_sql_write
from llm_agents.deep_agents import events

from llm_agents.deep_agents.planning.store.base import _StoreBase
from llm_agents.deep_agents.planning.store.models import (
    CRITICAL_KEY, IGNORE_NOTE, PLAN_NODE, TICK_STATUSES,
    _STATE_COLUMNS, _NOTE_MAX, _kind, _tokenize_tags_sync,
    PlannedStep, StateRow, ThreadPlan, _normalize_notes, extract_critical,
)


class StateStoreMixin(_StoreBase):
    """The llm.deep_agent_states lifecycle: begin → pending steps →
    ticks → child states → settle."""

    async def begin_thread(self,
        thread_id: str, *, title: str = "Plan",
        super_thread_id: Optional[str] = None,
    ) -> Optional[int]:
        """Insert the run's RUNNING PLANNING NODE — the ordinal-0 top-level
        state row (node 'plan', in_progress) written at RUN START, before
        the plan LLM call. It is what makes the run visible the moment it
        begins: llm.deep_agent_states is the summary history's source, so
        the Generate click's record (event date + time) exists immediately,
        and the thread read stops 404ing while the plan phase is still
        thinking — the UI opens the graph straight onto a running planning
        node instead of a waiting state. The plan node closes the row
        (settle_plan_state) when the proposal lands; a run stopped
        mid-planning leaves it to the stop settle (in_progress → failed
        'stopped by user'). *super_thread_id* stamps the CHAIN id (the
        three-graph refactor: the plan thread IS the chain's root, so its
        own rows carry its own id). Returns the state id (the runner stamps
        the plan node's LLM context onto the row), or None when the insert
        degraded — never worth failing the run over."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/insert_plan_running",
                thread_id, super_thread_id, PLAN_NODE, title[:_NOTE_MAX],
            )
            state_id = await conn.fetchval(write.sql, *write.params)
        finally:
            await conn.close()
        if state_id is None:
            return None
        events.publish(events.DeepAgentEvent(
            type=events.EventType.STATE_CREATED,
            thread_id=thread_id,
            state_id=int(state_id),
            node=PLAN_NODE,
            title=title[:_NOTE_MAX],
            status="in_progress",
            ordinal=0,
        ))
        return int(state_id)


    async def settle_plan_state(self,
        thread_id: str,
        status: str,
        *,
        note: Optional[str] = None,
        error: Optional[str] = None,
        llm_context_id: Optional[str] = None,
    ) -> None:
        """Close the running planning node (begin_thread's row) when the
        plan phase ends: completed with the step-count note, or failed with
        the proposal/persistence reason. Only an in_progress planning row
        moves (idempotent — the blanket finalize/stop settle owns the row
        once this could not); the note lands as a step-result context like
        any tick note, keyed to the plan node's ONE chained context
        (*llm_context_id* — the plan conversation that produced it) and the
        planning row it closes."""
        if status not in ("completed", "failed"):
            raise ValueError(
                f"plan settle status must be completed|failed, got {status!r}")
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/settle_plan_node",
                thread_id, status,
                str(error)[:_NOTE_MAX] if error else None, PLAN_NODE,
            )
            state_id = await conn.fetchval(write.sql, *write.params)
            payload_note = note if status == "completed" else error
            if state_id is not None and payload_note is not None:
                result_context_id = await self._insert_context(
                    conn, thread_id, "step-result",
                    json.dumps({"note": str(payload_note)[:_NOTE_MAX]},
                               ensure_ascii=False),
                    node=PLAN_NODE, state_id=int(state_id),
                    llm_context_id=llm_context_id)
                write = render_sql_write(
                    "llm_agents/deep_agents/planning/store/"
                    "deep_agent_states/set_result_context",
                    state_id, result_context_id,
                )
                await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()
        if state_id is not None:
            events.publish(events.DeepAgentEvent(
                type=events.EventType.STATE_UPDATED,
                thread_id=thread_id,
                state_id=int(state_id),
                node=PLAN_NODE,
                status=status,
            ))


    async def open_custom_thread(self,
        plan_thread_id: str,
        plan_payload: Dict[str, Any],
        custom_thread_id: str,
        steps: Sequence[PlannedStep],
        *,
        super_thread_id: Optional[str] = None,
        plan_state_id: Optional[int] = None,
        llm_context_id: Optional[str] = None,
    ) -> ThreadPlan:
        """Open the chain's first two threads' rows in ONE connection (the
        batched proposal's first validated batch — the former create_thread,
        split across the plan/custom threads by the three-graph refactor):
        the PLAN thread's (partial) plan context row + the CUSTOM thread's
        pending step rows, so the UI's graph fills during planning and a
        failure lands NEITHER (the degrade stays clean). Later batches
        append through :func:`add_plan_states` on the custom thread; the
        complete plan payload lands at the plan graph's output node
        (:func:`replace_plan_payload`)."""
        conn = await self._connect()
        states: List[StateRow] = []
        try:
            await self._insert_context(
                conn, plan_thread_id, "plan",
                json.dumps(plan_payload, ensure_ascii=False),
                node=PLAN_NODE, state_id=plan_state_id,
                llm_context_id=llm_context_id)
            for ordinal, step in enumerate(steps, start=1):
                input_context_id = None
                notes_json = _normalize_notes(
                    step.notes, step.delegate, step.parallel)
                if notes_json is not None:
                    input_context_id = await self._insert_context(
                        conn, custom_thread_id, "step-input", notes_json,
                        node=step.node, llm_context_id=llm_context_id)
                write = render_sql_write(
                    "llm_agents/deep_agents/planning/store/"
                    "deep_agent_states/insert_pending_step",
                    custom_thread_id, super_thread_id, step.node, step.title,
                    ordinal, input_context_id,
                )
                record = await conn.fetchrow(write.sql, *write.params)
                state = self._state_row(record)
                states.append(state)
                events.publish(events.DeepAgentEvent(
                    type=events.EventType.STATE_CREATED,
                    thread_id=custom_thread_id,
                    state_id=state.state_id,
                    node=state.node,
                    title=state.title,
                    status=state.status,
                    ordinal=state.ordinal,
                ))
            await self._backfill_input_state_ids(conn, custom_thread_id)
        finally:
            await conn.close()
        return ThreadPlan(thread_id=custom_thread_id, steps=list(steps),
                          states=states)


    async def record_plan_context(self,
        thread_id: str,
        plan_payload: Dict[str, Any],
        *,
        plan_state_id: Optional[int] = None,
        llm_context_id: Optional[str] = None,
    ) -> None:
        """The PLAN GRAPH's final output row: the thread's ONE kind 'plan'
        context payload (the complete plan JSON — goal, args, ladder
        outcome, steps — what the Thread Graph's plan panel reads and the
        Save button templates). In the three-graph chain this row lives in
        the PLAN thread (the chain's root); the step rows it describes live
        in the CUSTOM thread. Keyed to the RUNNING PLANNING NODE's row
        (*plan_state_id* — begin_thread's) and the plan conversation's ONE
        chained context."""
        conn = await self._connect()
        try:
            await self._insert_context(
                conn, thread_id, "plan",
                json.dumps(plan_payload, ensure_ascii=False),
                node=PLAN_NODE, state_id=plan_state_id,
                llm_context_id=llm_context_id)
        finally:
            await conn.close()


    async def create_thread_states(self,
        thread_id: str,
        steps: Sequence[PlannedStep],
        *,
        super_thread_id: Optional[str] = None,
        llm_context_id: Optional[str] = None,
    ) -> List[StateRow]:
        """One graph's STEP ROWS: one pending state per step (its `notes`
        as the step's input context), stamped with the run's CHAIN id —
        the custom graph's plan steps and the result graph's review-wave
        seats both land through this (the former create_thread's step half;
        the plan context row moved to :func:`record_plan_context` in the
        plan thread). Each step-input entry keys to its own step node and
        the plan conversation's ONE chained context."""
        conn = await self._connect()
        states: List[StateRow] = []
        try:
            for ordinal, step in enumerate(steps, start=1):
                input_context_id = None
                notes_json = _normalize_notes(
                    step.notes, step.delegate, step.parallel)
                if notes_json is not None:
                    input_context_id = await self._insert_context(
                        conn, thread_id, "step-input", notes_json,
                        node=step.node, llm_context_id=llm_context_id)
                write = render_sql_write(
                    "llm_agents/deep_agents/planning/store/"
                    "deep_agent_states/insert_pending_step",
                    thread_id, super_thread_id, step.node, step.title,
                    ordinal, input_context_id,
                )
                record = await conn.fetchrow(write.sql, *write.params)
                state = self._state_row(record)
                states.append(state)
                events.publish(events.DeepAgentEvent(
                    type=events.EventType.STATE_CREATED,
                    thread_id=thread_id,
                    state_id=state.state_id,
                    node=state.node,
                    title=state.title,
                    status=state.status,
                    ordinal=state.ordinal,
                ))
            await self._backfill_input_state_ids(conn, thread_id)
        finally:
            await conn.close()
        return states


    async def replace_plan_payload(self,
        thread_id: str,
        plan_payload: Dict[str, Any],
        *,
        llm_context_id: Optional[str] = None,
    ) -> None:
        """Rewrite the thread's ONE 'plan' context row's payload in place —
        the batched proposal session's final write (the runner persists
        step states batch by batch while the proposal streams, then lands
        the COMPLETE plan payload — full steps array, the ladder's outcome,
        provenance — over the partial one the first batch wrote). The row
        keeps its original identity/keys (state/llm-context links); a
        missing row (create_thread degraded earlier) inserts one, so the
        UI's plan read never 404s on a batched thread. Fail-soft by
        contract of the callers: raises here, the runner wraps it."""
        payload_json = json.dumps(plan_payload, ensure_ascii=False)
        conn = await self._connect()
        try:
            # Tags refresh with the payload (the plan grew — the vector
            # must cover the complete steps, not the first batch's partial).
            tokens = await asyncio.to_thread(
                _tokenize_tags_sync, payload_json)
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_contexts/replace_plan_payload",
                thread_id, payload_json, _kind("plan"), tokens)
            updated = await conn.fetchval(write.sql, *write.params)
            if updated is None:
                await self._insert_context(
                    conn, thread_id, "plan", payload_json,
                    node=PLAN_NODE, llm_context_id=llm_context_id)
        finally:
            await conn.close()


    async def fetch_state(self, state_id: int) -> Optional[StateRow]:
        """One state row by id (the plan-tools handlers' authority checks —
        a tick or revise must see the row's CURRENT status first)."""
        conn = await self._connect()
        try:
            record = await conn.fetchrow(
                f"SELECT {_STATE_COLUMNS} FROM llm.deep_agent_states "
                f"WHERE state_id = $1", state_id)
        finally:
            await conn.close()
        return self._state_row(record) if record is not None else None


    async def fetch_step_delegate(self,
        state_id: int,
    ) -> Tuple[Optional[str], bool]:
        """A step's delegation goal and independence mark from its input
        context payload — the step-graph engine's fork/wave detector. Works
        for planned AND runtime-added steps (both store the flags in the
        step-input payload; goal None = an inline step, parallel False =
        ordered — only consecutive delegated-or-parallel steps wave)."""
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                "SELECT c.payload ->> 'delegate_goal' AS goal, "
                "COALESCE((c.payload ->> 'parallel')::boolean, false) "
                "AS parallel "
                "FROM llm.deep_agent_states s "
                "JOIN llm.deep_agent_contexts c "
                "  ON c.context_id = s.input_context_id "
                "WHERE s.state_id = $1", state_id)
        finally:
            await conn.close()
        if row is None:
            return None, False
        return (str(row["goal"]) if row["goal"] else None,
                bool(row["parallel"]))


    async def _thread_super(self, conn, thread_id: str) -> Optional[str]:
        """The chain id this thread's rows already carry — runtime adds
        (plan tools, fork children) inherit it instead of every caller
        threading the super id through (None on legacy threads)."""
        return await conn.fetchval(
            "SELECT super_thread_id FROM llm.deep_agent_states "
            "WHERE thread_id = $1 AND super_thread_id IS NOT NULL "
            "ORDER BY state_id LIMIT 1", thread_id)


    async def add_plan_states(self,
        thread_id: str,
        steps: Sequence[PlannedStep],
        *,
        parent_state_id: Optional[int] = None,
        after_ordinal: Optional[int] = None,
        llm_context_id: Optional[str] = None,
        super_thread_id: Optional[str] = None,
    ) -> List[StateRow]:
        """Insert new PENDING states at RUNTIME — the plan-update tool's
        write (a mid-run investigation add, parent- or subagent-initiated).

        Top-level steps (parent None) take ordinals ``after_ordinal + 1..``
        with later rows shifted up, or append past the current last ordinal
        when *after_ordinal* is None. Fork children carry ordinal 0 under
        their parent (sibling order is state_id, the create_child_state
        convention). *llm_context_id* stamps the conversation that owns the
        new nodes — the main execute context for main-lane adds, the
        subagent's own context for fork extensions. *super_thread_id* stamps
        the chain id; when None it derives from the thread's existing rows
        (the plan tools never know it)."""
        if not steps:
            return []
        conn = await self._connect()
        states: List[StateRow] = []
        try:
            if super_thread_id is None:
                super_thread_id = await self._thread_super(conn, thread_id)
            if parent_state_id is None:
                n = len(steps)
                if after_ordinal is not None:
                    write = render_sql_write(
                        "llm_agents/deep_agents/planning/store/"
                        "deep_agent_states/shift_ordinals",
                        thread_id, int(after_ordinal), n)
                    await conn.execute(write.sql, *write.params)
                    next_ordinal = int(after_ordinal) + 1
                else:
                    last = await conn.fetchval(
                        "SELECT COALESCE(MAX(ordinal), 0) "
                        "FROM llm.deep_agent_states "
                        "WHERE thread_id = $1 AND parent_state_id IS NULL",
                        thread_id)
                    next_ordinal = int(last) + 1
            else:
                next_ordinal = 0
            for offset, step in enumerate(steps):
                input_context_id = None
                notes_json = _normalize_notes(
                    step.notes, step.delegate, step.parallel)
                if notes_json is not None:
                    input_context_id = await self._insert_context(
                        conn, thread_id, "step-input", notes_json,
                        node=step.node, llm_context_id=llm_context_id)
                write = render_sql_write(
                    "llm_agents/deep_agents/planning/store/"
                    "deep_agent_states/insert_pending_step_fork",
                    thread_id, super_thread_id, parent_state_id, step.node,
                    step.title, next_ordinal + offset, input_context_id,
                    llm_context_id,
                )
                record = await conn.fetchrow(write.sql, *write.params)
                state = self._state_row(record)
                states.append(state)
                events.publish(events.DeepAgentEvent(
                    type=events.EventType.STATE_CREATED,
                    thread_id=thread_id,
                    state_id=state.state_id,
                    node=state.node,
                    title=state.title,
                    status=state.status,
                    ordinal=state.ordinal,
                    parent_state_id=state.parent_state_id,
                ))
            await self._backfill_input_state_ids(conn, thread_id)
        finally:
            await conn.close()
        return states


    async def revise_pending_state(self,
        state_id: int, *, title: Optional[str] = None,
        notes: Optional[Union[str, Dict[str, Any]]] = None,
        delegate: Optional[str] = None,
        llm_context_id: Optional[str] = None,
    ) -> Optional[StateRow]:
        """Rewrite one NOT-YET-RUN (pending) step — the plan-update tool's
        revise op. Only pending rows move (a step that started is history);
        a notes/delegate change mints a NEW step-input context and repoints
        the row (contexts are append-only), keyed to the step's node/task
        and the revising conversation's chained context. *delegate*
        semantics: None = untouched, ``''`` clears the fork marker, a string
        sets it. Returns the revised row, or None when the row is gone or
        not pending."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/revise_pending_title",
                state_id, title,
            )
            record = await conn.fetchrow(write.sql, *write.params)
            if record is None:
                return None
            if notes is not None or delegate is not None:
                current = self._state_row(record)
                # The independence mark survives a revise (the new payload
                # re-mints from the old one's flags — a goal/notes rewrite
                # never silently re-orders an independent step back into
                # the serial lane).
                keep_parallel = await conn.fetchval(
                    "SELECT COALESCE((c.payload ->> 'parallel')::boolean, "
                    "false) FROM llm.deep_agent_contexts c "
                    "WHERE c.context_id = $1", current.input_context_id)
                payload: Dict[str, Any] = json.loads(
                    _normalize_notes(notes, delegate,
                                     bool(keep_parallel)) or "{}")
                input_context_id = await self._insert_context(
                    conn, current.thread_id, "step-input",
                    json.dumps(payload, ensure_ascii=False),
                    node=current.node, state_id=state_id,
                    llm_context_id=llm_context_id)
                write = render_sql_write(
                    "llm_agents/deep_agents/planning/store/"
                    "deep_agent_states/set_input_context",
                    state_id, input_context_id,
                )
                record = await conn.fetchrow(write.sql, *write.params)
        finally:
            await conn.close()
        if record is not None:
            revised = self._state_row(record)
            events.publish(events.DeepAgentEvent(
                type=events.EventType.STATE_UPDATED,
                thread_id=revised.thread_id,
                state_id=revised.state_id,
                node=revised.node,
                status=revised.status,
                title=revised.title,
            ))
            return revised
        return None


    async def skip_pending_states(self,
        state_ids: Sequence[int], reason: str,
        *, thread_id: str = "",
    ) -> int:
        """Settle NOT-YET-RUN steps as skipped — the plan-update tool's
        remove op (a replan dropping pending work keeps the audit trail:
        the row stays, wearing the removal reason). Only pending rows move;
        returns how many actually settled."""
        if not state_ids:
            return 0
        conn = await self._connect()
        try:
            # fetchval would hand back None on a bare UPDATE — RETURNING
            # makes the count real.
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/skip_pending",
                [int(s) for s in state_ids], str(reason)[:_NOTE_MAX],
            )
            rows = await conn.fetch(write.sql, *write.params)
        finally:
            await conn.close()
        if rows and thread_id:
            events.publish(events.DeepAgentEvent(
                type=events.EventType.STATES_SETTLED,
                thread_id=thread_id,
                count=len(rows),
            ))
        return len(rows)


    async def reopen_state(self, state_id: int, *, thread_id: str = "") -> bool:
        """REPAIR LANE re-open (the supervisor's retry primitive, 2026-11-06):
        flip a FAILED state row back to pending — error/finished_at
        cleared (a later successful settle COALESCEs over error and would
        otherwise resurrect the failed attempt's message) — so the step
        lane's normal routing re-picks it next hop. Guarded failed→pending
        ONLY (a completed/skipped row never reopens; a concurrent settle
        makes the guard miss and this returns False). Returns whether the
        row actually reopened."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/reopen_failed",
                int(state_id),
            )
            rows = await conn.fetch(write.sql, *write.params)
        finally:
            await conn.close()
        if rows:
            events.publish(events.DeepAgentEvent(
                type=events.EventType.STATE_UPDATED,
                thread_id=thread_id,
                state_id=state_id,
                status="pending",
            ))
        return bool(rows)


    async def ignore_state(self, state_id: int, note: str = IGNORE_NOTE, *,
                           thread_id: str = "") -> bool:
        """REPAIR LANE ignore-settle (the supervisor's skip verdict,
        2026-11-06): flip a FAILED state row to skipped wearing the
        supervisor's note — the run proceeds PAST the step (its work is
        judged dispensable or the fault structural-but-non-fatal).
        Guarded failed→skipped ONLY (a pending/completed row never
        flips). Returns whether the row actually settled."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/ignore_failed",
                int(state_id), str(note)[:_NOTE_MAX],
            )
            rows = await conn.fetch(write.sql, *write.params)
        finally:
            await conn.close()
        if rows:
            events.publish(events.DeepAgentEvent(
                type=events.EventType.STATES_SETTLED,
                thread_id=thread_id,
                count=1,
            ))
        return bool(rows)


    async def tick_step(self,
        state_id: int,
        status: str,
        *,
        note: Optional[str] = None,
        error: Optional[str] = None,
        thread_id: str = "",
        node: Optional[str] = None,
        llm_context_id: Optional[str] = None,
        critical: bool = False,
    ) -> None:
        """One agent tick: in_progress stamps started_at; completed/failed
        stamp finished_at and store the tick note as the state's result
        context, keyed to the task (*state_id*, *node*) and the ticking
        conversation's ONE chained context (*llm_context_id* — the entry
        links that conversation's llm_requests rows). *error* (the failed
        settle's reason — a delegation failure, a step exception) lands on
        the row's error column (COALESCE — an existing error is never
        blanked). *critical* (the model's flag that the note carries a
        CRITICAL DISCOVERY — the fork tick tool's explicit argument, or the
        [CRITICAL] reply marker parsed by the caller) rides into the
        payload as "critical": true, which _insert_context lifts into the
        row's is_critical column. Unknown statuses raise (the caller
        validates)."""
        if status not in TICK_STATUSES:
            raise ValueError(f"tick status must be one of {TICK_STATUSES}, got {status!r}")
        conn = await self._connect()
        try:
            result_context_id = None
            if note is not None:
                payload: Dict[str, Any] = {"note": str(note)[:_NOTE_MAX]}
                if critical:
                    payload[CRITICAL_KEY] = True
                result_context_id = await self._insert_context(
                    conn, thread_id, "step-result",
                    json.dumps(payload, ensure_ascii=False),
                    node=node, state_id=state_id,
                    llm_context_id=llm_context_id)
            if status == "in_progress":
                write = render_sql_write(
                    "llm_agents/deep_agents/planning/store/"
                    "deep_agent_states/tick_start",
                    state_id,
                )
                await conn.execute(write.sql, *write.params)
            else:
                write = render_sql_write(
                    "llm_agents/deep_agents/planning/store/"
                    "deep_agent_states/tick_settle",
                    state_id, status, result_context_id,
                    str(error)[:_NOTE_MAX] if error else None,
                )
                await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()
        if thread_id:
            events.publish(events.DeepAgentEvent(
                type=events.EventType.STATE_UPDATED,
                thread_id=thread_id,
                state_id=int(state_id),
                status=status,
            ))


    async def create_child_state(self,
        thread_id: str, parent_state_id: Optional[int], node: str, title: str,
        *,
        llm_context_id: Optional[str] = None,
    ) -> int:
        """A spawned subagent's child state — already running under the
        step that was in_progress at spawn time (LangGraph's subgraph). The
        child mints its OWN llm_context_id (its conversation is not the
        parent node's); the registry item lands in the store when the
        output hook settles the child."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/insert_child_running",
                thread_id, await self._thread_super(conn, thread_id),
                parent_state_id, node, title, llm_context_id,
            )
            child_id = await conn.fetchval(write.sql, *write.params)
        finally:
            await conn.close()
        events.publish(events.DeepAgentEvent(
            type=events.EventType.STATE_CREATED,
            thread_id=thread_id,
            state_id=int(child_id),
            node=node,
            title=title,
            status="in_progress",
            ordinal=0,
            parent_state_id=parent_state_id,
        ))
        return int(child_id)


    async def stamp_states_context(self,
        state_ids: Sequence[int], llm_context_id: str,
    ) -> None:
        """Point plan-step states at their node's ONE context id — the
        execute node stamps its whole plan at entry (the agent loop walks
        the steps inside its single conversation). Idempotent: rows that
        already carry a context (a re-tick after a re-stamp) keep theirs."""
        if not state_ids:
            return
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/stamp_llm_context",
                [int(s) for s in state_ids], llm_context_id,
            )
            await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()


    async def finish_child_state(self,
        state_id: int, status: str, *,
        answer: Optional[str] = None,
        error: Optional[str] = None,
        thread_id: str = "",
        node: Optional[str] = None,
        llm_context_id: Optional[str] = None,
    ) -> None:
        """Settle a child state; the subagent's final answer (or error)
        becomes its subagent-result context, keyed to the fork's task
        (*state_id*, *node* — the fork slug) and the CHILD's OWN chained
        context (*llm_context_id* — the entry links the subagent
        conversation's llm_requests rows). The answer is the child's RAW
        final reply: a trailing [CRITICAL] marker line (the fork prompts'
        critical-discovery contract) is parsed out here — the row's
        is_critical lifts from the payload the marker produces. A FAILED
        child also ends its fork path: every pending node the subagent
        added under this fork settles skipped 'not run — its fork path
        failed' (the grey tail on the thread canvas), the same settle
        finalize would apply but immediate, so a dead fork's subtree never
        reads as to-be-run."""
        if status not in ("completed", "failed"):
            raise ValueError(f"child status must be completed|failed, got {status!r}")
        conn = await self._connect()
        try:
            payload: Optional[Dict[str, Any]] = None
            if answer is not None:
                critical, clean = extract_critical(str(answer))
                payload = {"answer": clean[:_NOTE_MAX]}
                if critical:
                    payload[CRITICAL_KEY] = True
            else:
                payload = {"error": str(error)[:_NOTE_MAX] if error else "failed"}
            result_context_id = await self._insert_context(
                conn, thread_id, "subagent-result",
                json.dumps(payload, ensure_ascii=False),
                node=node, state_id=state_id, llm_context_id=llm_context_id)
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/settle_child",
                state_id, status, result_context_id,
                str(error)[:_NOTE_MAX] if error else None,
            )
            await conn.execute(write.sql, *write.params)
            n_not_run = 0
            if status == "failed":
                # The whole subtree below the failed fork (a sub-sub fork's
                # own children included — the CTE walks parent_state_id to
                # the leaves), in case the dying subagent left unticked
                # nodes behind.
                write = render_sql_write(
                    "llm_agents/deep_agents/planning/store/"
                    "deep_agent_states/skip_failed_subtree",
                    state_id,
                )
                record = await conn.fetchrow(write.sql, *write.params)
                n_not_run = 1 if record is not None else 0
        finally:
            await conn.close()
        if thread_id:
            events.publish(events.DeepAgentEvent(
                type=events.EventType.STATE_UPDATED,
                thread_id=thread_id,
                state_id=int(state_id),
                status=status,
            ))
            if n_not_run:
                events.publish(events.DeepAgentEvent(
                    type=events.EventType.STATES_SETTLED,
                    thread_id=thread_id,
                ))
