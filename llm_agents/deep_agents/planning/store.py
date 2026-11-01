"""llm_agents.deep_agents.planning.store — thread plan/state persistence.

The DB side of the deep-agent thread design: ``text.deep_agent_states``
(one row per graph node/task state of a run, each referencing its LLM
context by ``llm_context_id`` — a reference only, the context data
lives nowhere by design),
``text.deep_agent_contexts`` (the JSONB payloads states point into —
ONE ENTRY per thread per node per task/step, each keyed by its node,
its task row and the chained llm_context_id of the node conversation
that produced it, so the payloads chain under their node's ONE context
and link its ``text.llm_requests`` rows) and
``text.deep_agent_identities`` (the code <-> thread_id link — one row
per scoped run, the reverse index the summary history's per-code read
filters on). The LangGraph STORE's items live beside these in
``text.deep_agent_store_items`` (``state_store.py`` — node states and
the context registry). DDL ``database/sql/text/09_deep_agent_states.sql``
+ ``12_deep_agent_store.sql`` — LangGraph's thread / node / task /
state / subgraph concepts without the framework.

Every function here is a PLAIN accessor (it raises on DB failure); the
degrade-to-plain-run policy lives in the runner (``runner.py``), which
wraps each call fail-soft — a DB outage must never fail the summary,
the same never-block contract as ``llm_agents._core.request_log``.

BESIDE the initial create_thread insert, the plan is MUTABLE at runtime
(``plan_tools.py`` holds the permission model): ``add_plan_states``
appends pending steps mid-run (main-lane or under a fork),
``revise_pending_state`` rewrites a not-yet-run step,
``skip_pending_states`` drops pending steps (audit-preserving), and
``fetch_state`` / ``list_thread_states`` read the CURRENT tree the
tick/update handlers resolve against. The MAIN thread owns pending
top-level nodes and may terminate running forks; a subagent owns only
the nodes it minted under its own forked path.

Write volume is tiny (a handful of rows per run), so everything is the
plain-SQL rung: one short-lived asyncpg connection per call via
``_common.db_commons`` (imported lazily — the activation-order rule the
other llm_agents modules follow).

Every write ALSO announces a deep-agent event (``events.publish`` — the
daemon's /ws WebSocket channel): the UI's live views are push-driven,
with its poll loops as the fallback. The publishes are fire-and-forget
announcements beside the write, never a part of its contract.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from llm_agents.deep_agents import events

# The only statuses the agent-facing tick tool may set; finalize also
# settles with skipped (see finalize_thread).
TICK_STATUSES = ("in_progress", "completed", "failed")

_NOTE_MAX = 500
# Context hygiene: plan step `notes` (what the step must confirm) are the
# ONLY context payload without a writer-side cap (titles 300, delegate
# goals 1500, tick notes 500) — bound them at the _normalize_notes
# boundary so a runaway plan cannot bloat context rows.
_NOTES_MAX = 2000

# The runner-lane PLAN node's work tag on state rows: begin_thread's
# ordinal-0 planning row (and nothing else) carries it — the row that
# makes a run visible from its first instant. Consumers that mean "the
# model-planned steps" (the authorities' main lane, the UI's step-chain
# canvas) filter on it.
PLAN_NODE = "plan"

# The runner-lane RESULT node's tag on context rows (state_store's
# NODE_RESULT mirror — planning must not import graph): the hardcoded
# final-result node keeps no state row, so its entries key on this.
RESULT_NODE = "result"


@dataclass(frozen=True)
class PromptRow:
    """One active plan prompt (text.ai_prompts, prompt_type 2)."""

    prompt_key: str
    version: int
    instruction: str


@dataclass(frozen=True)
class PlannedStep:
    """One validated step of the model's plan JSON.

    ``delegate`` (the delegation goal) marks a FORK step: the execute
    node's agent runs it by spawning a subagent with this self-contained
    goal instead of working it inline — the subagent's child state (and
    any nodes the subagent itself adds under it) is the forked path of
    the step. None = an ordinary inline step.

    ``parallel`` marks an INDEPENDENT step: one whose work does not need
    any earlier step's findings, so consecutive parallel/delegated steps
    may execute as ONE concurrent wave (the runner's fork-wave lane). A
    delegated step is parallel by construction; the flag is how an
    INLINE step joins a wave."""

    node: str
    title: str
    notes: Optional[Union[str, Dict[str, Any]]] = None
    delegate: Optional[str] = None
    parallel: bool = False


@dataclass(frozen=True)
class StateRow:
    """One text.deep_agent_states row (the snapshot/list shape)."""

    state_id: int
    thread_id: str
    parent_state_id: Optional[int]
    node: str
    title: str
    status: str
    ordinal: int
    input_context_id: Optional[int]
    result_context_id: Optional[int]
    error: Optional[str]
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    # The LLM context this state ran under (the store's registry key) —
    # a REFERENCE; the context data itself is never persisted.
    llm_context_id: Optional[str] = None


@dataclass
class ThreadPlan:
    """The persisted plan of one thread — steps + their state rows."""

    thread_id: str
    steps: List[PlannedStep] = field(default_factory=list)
    states: List[StateRow] = field(default_factory=list)

    def state_by_ordinal(self, ordinal: int) -> Optional[StateRow]:
        for state in self.states:
            if state.ordinal == ordinal:
                return state
        return None


@dataclass(frozen=True)
class ThreadIdentity:
    """One scoped run's subject — the classification-nav pick that
    focused the summary (the request's ``data.scope``), as the
    text.deep_agent_identities row's values."""

    sec_type: int  # static.label_dim 'sec_type' code (etf=0 index=1 stock=2 industry=3)
    code: str      # security code (index/etf/stock) or industry id
    name: Optional[str] = None


def _kind(label: str) -> int:
    """The context row's kind as the static.label_dim
    'agent_context_kind' SMALLINT code (closed vocabulary: plan /
    step-input / step-result / subagent-result / final-result)."""
    from _common.labels import AGENT_CONTEXT_KIND_CODE  # lazy: numpy

    return AGENT_CONTEXT_KIND_CODE[label]


def _normalize_notes(
    notes: Optional[Union[str, Dict[str, Any]]],
    delegate: Optional[str] = None,
    parallel: bool = False,
) -> Optional[str]:
    """The JSONB text for a step's notes (str -> {"note": str}); a
    delegation goal rides along as {"delegate_goal": ...} and the
    independence mark as {"parallel": true} so the step's input context
    carries what the planner wrote. The note text is capped (_NOTES_MAX
    — context hygiene: the entry keeps the important data, bounded)."""
    if notes is None and delegate is None and not parallel:
        return None
    payload: Dict[str, Any] = {}
    if notes is not None:
        payload["note"] = (json.dumps(notes, ensure_ascii=False)
                           if isinstance(notes, dict) else str(notes))[:_NOTES_MAX]
    if delegate:
        payload["delegate_goal"] = delegate
    if parallel:
        payload["parallel"] = True
    return json.dumps(payload, ensure_ascii=False)


async def _insert_context(
    conn, thread_id: str, kind_label: str, payload_json: str, *,
    node: Optional[str] = None, state_id: Optional[int] = None,
    llm_context_id: Optional[str] = None,
) -> int:
    """Insert one text.deep_agent_contexts row with its chained-context
    keys — the ONE place the table is written. One entry = one payload
    per thread per node per task/step: *node* names the entry's node,
    *state_id* its task row, *llm_context_id* the ONE chained LLM
    context (uuid hex) of the node execution whose conversation produced
    it — the key that links the entry to its text.llm_requests rows
    (llm_requests.chain_id). Returns the new context id."""
    context_id = await conn.fetchval(
        "INSERT INTO text.deep_agent_contexts "
        "(thread_id, kind, payload, node, state_id, llm_context_id) "
        "VALUES ($1, $2, $3::jsonb, $4, $5, $6) RETURNING context_id",
        thread_id, _kind(kind_label), payload_json,
        node, state_id, llm_context_id,
    )
    return int(context_id)


async def _backfill_input_state_ids(conn, thread_id: str) -> None:
    """Point this thread's unlinked step-input context rows at their
    states: a state insert needs its input_context_id FIRST, so the
    entries mint before their state rows exist — this one scoped UPDATE
    links them after the inserts (and heals any legacy unlinked rows of
    the thread the same way)."""
    await conn.execute(
        "UPDATE text.deep_agent_contexts c SET state_id = s.state_id "
        "FROM text.deep_agent_states s "
        "WHERE s.thread_id = $1 AND s.input_context_id = c.context_id "
        "AND c.state_id IS NULL AND c.kind = $2",
        thread_id, _kind("step-input"),
    )


def _state_row(record) -> StateRow:
    return StateRow(
        state_id=int(record[0]),
        thread_id=record[1],
        parent_state_id=int(record[2]) if record[2] is not None else None,
        node=record[3],
        title=record[4],
        status=record[5],
        ordinal=int(record[6]),
        input_context_id=int(record[7]) if record[7] is not None else None,
        result_context_id=int(record[8]) if record[8] is not None else None,
        error=record[9],
        started_at=str(record[10]) if record[10] is not None else None,
        finished_at=str(record[11]) if record[11] is not None else None,
        llm_context_id=record[12] if len(record) > 12 else None,
    )


_STATE_COLUMNS = (
    "state_id, thread_id, parent_state_id, node, title, status, ordinal, "
    "input_context_id, result_context_id, error, started_at, finished_at, "
    "llm_context_id"
)


async def _connect():
    from _common.db_commons import get_db_connection_async

    return await get_db_connection_async()


async def fetch_plan_prompts(prompt_keys: Sequence[str]) -> List[PromptRow]:
    """The active plan-type rows (prompt_type 2) among *prompt_keys*."""
    conn = await _connect()
    try:
        records = await conn.fetch(
            "SELECT prompt_key, version, instruction FROM text.ai_prompts "
            "WHERE prompt_key = ANY($1::text[]) AND prompt_type = 2 "
            "AND is_active ORDER BY ordinal, prompt_key",
            list(prompt_keys),
        )
    finally:
        await conn.close()
    return [
        PromptRow(prompt_key=r[0], version=int(r[1]), instruction=r[2])
        for r in records
    ]


# The deep agent's INTERNAL prompt rows (prompt_type 3 'internal',
# seeded from the inline Python constants by
# database/sql/text/15_deep_agent_prompts.sql) — the DB-managed
# overrides the runner resolves at run start. Each consumer falls back
# to its built-in constant when its key is missing; the seed and the
# constants must stay in sync (the seed regenerates from them).
AGENT_PROMPT_PLAN_SYSTEM = "deep-agent-plan-system"
AGENT_PROMPT_STEP_ADDENDUM = "deep-agent-step-addendum"
AGENT_PROMPT_SUBAGENT_SYSTEM = "deep-agent-subagent-system"
AGENT_PROMPT_FORK_PLAN_ADDENDUM = "deep-agent-fork-plan-addendum"
AGENT_PROMPT_FORK_SPAWN_ADDENDUM = "deep-agent-fork-spawn-addendum"


async def fetch_agent_prompts() -> Dict[str, str]:
    """The ACTIVE internal agent-prompt rows (prompt_type 3) keyed by
    prompt_key — the DB-managed overrides of the deep agent's inline
    prompt constants: the planner's system prompt, the per-step
    addendum, and the forked child's base prompt and addenda. A missing
    key = the consumer's built-in constant; the caller wraps the fetch
    fail-soft (prompt sourcing never fails a run, the same contract as
    fetch_system_prompt)."""
    conn = await _connect()
    try:
        records = await conn.fetch(
            "SELECT prompt_key, instruction FROM text.ai_prompts "
            "WHERE prompt_type = 3 AND is_active")
    finally:
        await conn.close()
    return {str(r[0]): str(r[1]) for r in records}


async def begin_thread(thread_id: str, *, title: str = "Plan") -> Optional[int]:
    """Insert the run's RUNNING PLANNING NODE — the ordinal-0 top-level
    state row (node 'plan', in_progress) written at RUN START, before
    the plan LLM call. It is what makes the run visible the moment it
    begins: text.deep_agent_states is the summary history's source, so
    the Generate click's record (event date + time) exists immediately,
    and the thread read stops 404ing while the plan phase is still
    thinking — the UI opens the graph straight onto a running planning
    node instead of a waiting state. The plan node closes the row
    (settle_plan_state) when the proposal lands; a run stopped
    mid-planning leaves it to the stop settle (in_progress → failed
    'stopped by user'). Returns the state id (the runner stamps the plan
    node's LLM context onto the row), or None when the insert degraded —
    never worth failing the run over."""
    conn = await _connect()
    try:
        state_id = await conn.fetchval(
            "INSERT INTO text.deep_agent_states "
            "(thread_id, node, title, status, ordinal, started_at) "
            "VALUES ($1, $2, $3, 'in_progress', 0, now()) "
            "RETURNING state_id",
            thread_id, PLAN_NODE, title[:_NOTE_MAX],
        )
    finally:
        await conn.close()
    if state_id is None:
        return None
    events.publish({
        "type": "state_created", "thread_id": thread_id,
        "state_id": int(state_id), "node": PLAN_NODE,
        "title": title[:_NOTE_MAX], "status": "in_progress",
        "ordinal": 0, "parent_state_id": None, "ts": events.now_iso(),
    })
    return int(state_id)


async def settle_plan_state(
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
    conn = await _connect()
    try:
        state_id = await conn.fetchval(
            "UPDATE text.deep_agent_states SET status = $2, "
            "finished_at = now(), error = $3 "
            "WHERE thread_id = $1 AND node = $4 AND parent_state_id IS NULL "
            "AND status = 'in_progress' RETURNING state_id",
            thread_id, status,
            str(error)[:_NOTE_MAX] if error else None, PLAN_NODE,
        )
        payload_note = note if status == "completed" else error
        if state_id is not None and payload_note is not None:
            result_context_id = await _insert_context(
                conn, thread_id, "step-result",
                json.dumps({"note": str(payload_note)[:_NOTE_MAX]},
                           ensure_ascii=False),
                node=PLAN_NODE, state_id=int(state_id),
                llm_context_id=llm_context_id)
            await conn.execute(
                "UPDATE text.deep_agent_states SET result_context_id = $2 "
                "WHERE state_id = $1", state_id, result_context_id)
    finally:
        await conn.close()
    if state_id is not None:
        events.publish({
            "type": "state_updated", "thread_id": thread_id,
            "state_id": int(state_id), "node": PLAN_NODE,
            "status": status, "ts": events.now_iso(),
        })


async def create_thread(
    thread_id: str,
    plan_payload: Dict[str, Any],
    steps: Sequence[PlannedStep],
    *,
    plan_state_id: Optional[int] = None,
    llm_context_id: Optional[str] = None,
) -> ThreadPlan:
    """Persist one thread's plan: the plan context row + one pending
    state per step (its `notes` as the step's input context). The plan
    row keys to the RUNNING PLANNING NODE's row (*plan_state_id* —
    begin_thread's) and the plan conversation's ONE chained context
    (*llm_context_id*); each step-input entry keys to its own step node
    and — as the plan conversation proposed it — the same plan context."""
    conn = await _connect()
    states: List[StateRow] = []
    try:
        # The plan payload row is reached by kind (the UI's plan read),
        # not by a state pointer — nothing consumes its id here.
        await _insert_context(
            conn, thread_id, "plan",
            json.dumps(plan_payload, ensure_ascii=False),
            node=PLAN_NODE, state_id=plan_state_id,
            llm_context_id=llm_context_id)
        for ordinal, step in enumerate(steps, start=1):
            input_context_id = None
            notes_json = _normalize_notes(
                step.notes, step.delegate, step.parallel)
            if notes_json is not None:
                input_context_id = await _insert_context(
                    conn, thread_id, "step-input", notes_json,
                    node=step.node, llm_context_id=llm_context_id)
            record = await conn.fetchrow(
                f"INSERT INTO text.deep_agent_states "
                f"(thread_id, node, title, ordinal, input_context_id) "
                f"VALUES ($1, $2, $3, $4, $5) RETURNING {_STATE_COLUMNS}",
                thread_id, step.node, step.title, ordinal, input_context_id,
            )
            state = _state_row(record)
            states.append(state)
            events.publish({
                "type": "state_created", "thread_id": thread_id,
                "state_id": state.state_id, "node": state.node,
                "title": state.title, "status": state.status,
                "ordinal": state.ordinal, "parent_state_id": None,
                "ts": events.now_iso(),
            })
        await _backfill_input_state_ids(conn, thread_id)
    finally:
        await conn.close()
    return ThreadPlan(thread_id=thread_id, steps=list(steps), states=states)


async def fetch_state(state_id: int) -> Optional[StateRow]:
    """One state row by id (the plan-tools handlers' authority checks —
    a tick or revise must see the row's CURRENT status first)."""
    conn = await _connect()
    try:
        record = await conn.fetchrow(
            f"SELECT {_STATE_COLUMNS} FROM text.deep_agent_states "
            f"WHERE state_id = $1", state_id)
    finally:
        await conn.close()
    return _state_row(record) if record is not None else None


async def fetch_step_delegate(
    state_id: int,
) -> Tuple[Optional[str], bool]:
    """A step's delegation goal and independence mark from its input
    context payload — the step-graph engine's fork/wave detector. Works
    for planned AND runtime-added steps (both store the flags in the
    step-input payload; goal None = an inline step, parallel False =
    ordered — only consecutive delegated-or-parallel steps wave)."""
    conn = await _connect()
    try:
        row = await conn.fetchrow(
            "SELECT c.payload ->> 'delegate_goal' AS goal, "
            "COALESCE((c.payload ->> 'parallel')::boolean, false) "
            "AS parallel "
            "FROM text.deep_agent_states s "
            "JOIN text.deep_agent_contexts c "
            "  ON c.context_id = s.input_context_id "
            "WHERE s.state_id = $1", state_id)
    finally:
        await conn.close()
    if row is None:
        return None, False
    return (str(row["goal"]) if row["goal"] else None,
            bool(row["parallel"]))


async def add_plan_states(
    thread_id: str,
    steps: Sequence[PlannedStep],
    *,
    parent_state_id: Optional[int] = None,
    after_ordinal: Optional[int] = None,
    llm_context_id: Optional[str] = None,
) -> List[StateRow]:
    """Insert new PENDING states at RUNTIME — the plan-update tool's
    write (a mid-run investigation add, parent- or subagent-initiated).

    Top-level steps (parent None) take ordinals ``after_ordinal + 1..``
    with later rows shifted up, or append past the current last ordinal
    when *after_ordinal* is None. Fork children carry ordinal 0 under
    their parent (sibling order is state_id, the create_child_state
    convention). *llm_context_id* stamps the conversation that owns the
    new nodes — the main execute context for main-lane adds, the
    subagent's own context for fork extensions."""
    if not steps:
        return []
    conn = await _connect()
    states: List[StateRow] = []
    try:
        if parent_state_id is None:
            n = len(steps)
            if after_ordinal is not None:
                await conn.execute(
                    "UPDATE text.deep_agent_states SET ordinal = ordinal + $3 "
                    "WHERE thread_id = $1 AND parent_state_id IS NULL "
                    "AND ordinal > $2",
                    thread_id, int(after_ordinal), n)
                next_ordinal = int(after_ordinal) + 1
            else:
                last = await conn.fetchval(
                    "SELECT COALESCE(MAX(ordinal), 0) "
                    "FROM text.deep_agent_states "
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
                input_context_id = await _insert_context(
                    conn, thread_id, "step-input", notes_json,
                    node=step.node, llm_context_id=llm_context_id)
            record = await conn.fetchrow(
                f"INSERT INTO text.deep_agent_states "
                f"(thread_id, parent_state_id, node, title, ordinal, "
                f"input_context_id, llm_context_id) "
                f"VALUES ($1, $2, $3, $4, $5, $6, $7) "
                f"RETURNING {_STATE_COLUMNS}",
                thread_id, parent_state_id, step.node, step.title,
                next_ordinal + offset, input_context_id, llm_context_id,
            )
            state = _state_row(record)
            states.append(state)
            events.publish({
                "type": "state_created", "thread_id": thread_id,
                "state_id": state.state_id, "node": state.node,
                "title": state.title, "status": state.status,
                "ordinal": state.ordinal,
                "parent_state_id": state.parent_state_id,
                "ts": events.now_iso(),
            })
        await _backfill_input_state_ids(conn, thread_id)
    finally:
        await conn.close()
    return states


async def revise_pending_state(
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
    conn = await _connect()
    try:
        record = await conn.fetchrow(
            f"UPDATE text.deep_agent_states SET title = COALESCE($2, title) "
            f"WHERE state_id = $1 AND status = 'pending' "
            f"RETURNING {_STATE_COLUMNS}",
            state_id, title,
        )
        if record is None:
            return None
        if notes is not None or delegate is not None:
            current = _state_row(record)
            # The independence mark survives a revise (the new payload
            # re-mints from the old one's flags — a goal/notes rewrite
            # never silently re-orders an independent step back into
            # the serial lane).
            keep_parallel = await conn.fetchval(
                "SELECT COALESCE((c.payload ->> 'parallel')::boolean, "
                "false) FROM text.deep_agent_contexts c "
                "WHERE c.context_id = $1", current.input_context_id)
            payload: Dict[str, Any] = json.loads(
                _normalize_notes(notes, delegate,
                                 bool(keep_parallel)) or "{}")
            input_context_id = await _insert_context(
                conn, current.thread_id, "step-input",
                json.dumps(payload, ensure_ascii=False),
                node=current.node, state_id=state_id,
                llm_context_id=llm_context_id)
            record = await conn.fetchrow(
                f"UPDATE text.deep_agent_states SET input_context_id = $2 "
                f"WHERE state_id = $1 RETURNING {_STATE_COLUMNS}",
                state_id, input_context_id,
            )
    finally:
        await conn.close()
    if record is not None:
        revised = _state_row(record)
        events.publish({
            "type": "state_updated", "thread_id": revised.thread_id,
            "state_id": revised.state_id, "node": revised.node,
            "status": revised.status, "title": revised.title,
            "ts": events.now_iso(),
        })
        return revised
    return None


async def skip_pending_states(
    state_ids: Sequence[int], reason: str,
    *, thread_id: str = "",
) -> int:
    """Settle NOT-YET-RUN steps as skipped — the plan-update tool's
    remove op (a replan dropping pending work keeps the audit trail:
    the row stays, wearing the removal reason). Only pending rows move;
    returns how many actually settled."""
    if not state_ids:
        return 0
    conn = await _connect()
    try:
        # fetchval would hand back None on a bare UPDATE — RETURNING
        # makes the count real.
        rows = await conn.fetch(
            "UPDATE text.deep_agent_states SET status = 'skipped', "
            "finished_at = now(), error = $2 "
            "WHERE state_id = ANY($1::bigint[]) AND status = 'pending' "
            "RETURNING state_id",
            [int(s) for s in state_ids], str(reason)[:_NOTE_MAX],
        )
    finally:
        await conn.close()
    if rows and thread_id:
        events.publish({"type": "states_settled", "thread_id": thread_id,
                        "count": len(rows), "ts": events.now_iso()})
    return len(rows)


async def fetch_system_prompt() -> Optional[str]:
    """The run's standing system prompt: the ACTIVE system-type row of
    text.ai_prompts (prompt_type 0, lowest ordinal) — the DB-managed
    override for agent.DEFAULT_SYSTEM_PROMPT. None = no system row (the
    runner falls back to the built-in default). The runner renders the
    text as a template with the run's input args before use."""
    conn = await _connect()
    try:
        record = await conn.fetchrow(
            "SELECT instruction FROM text.ai_prompts "
            "WHERE prompt_type = 0 AND is_active "
            "ORDER BY ordinal, prompt_key LIMIT 1",
        )
    finally:
        await conn.close()
    return str(record[0]) if record is not None else None


def identity_from_scope(
    data: Optional[Dict[str, Any]],
) -> Optional[ThreadIdentity]:
    """The run's scope pick (the request's structured ``data.scope``) as
    a :class:`ThreadIdentity`, or None when the run is market-wide (no
    scope) or the pick is malformed — the identity row is bookkeeping,
    never worth failing a run over. The scope shape is the analysis
    summary bridge's: ``{"secType": ..., "code" | "industry", "name"?}``
    (services/ai-summary.service.ts)."""
    scope = (data or {}).get("scope")
    if not isinstance(scope, dict):
        return None
    from _common.labels import SEC_TYPE_CODE  # lazy: the module pulls numpy

    sec_type = SEC_TYPE_CODE.get(str(scope.get("secType") or "").strip())
    code = str(scope.get("code") or scope.get("industry") or "").strip()
    if sec_type is None or not code:
        return None
    name = scope.get("name")
    return ThreadIdentity(
        sec_type=int(sec_type),
        code=code,
        name=str(name).strip() if isinstance(name, str) and name.strip() else None,
    )


async def record_thread_identity(
    thread_id: str, identity: ThreadIdentity
) -> None:
    """Link the run's scope subject to its thread id — one
    text.deep_agent_identities row (the code <-> thread_id reverse index
    the summary history's per-code read filters on). Called by
    ``run_thread`` at RUN START from the request's classification-nav
    scope pick, so every generate sets the link — plan persisted or not
    (rows whose thread never planned simply never join to states).
    Market-wide runs (no scope pick) create no row."""
    conn = await _connect()
    try:
        await conn.execute(
            "INSERT INTO text.deep_agent_identities "
            "(thread_id, sec_type, code, name, date) "
            "VALUES ($1, $2, $3, $4, (now() AT TIME ZONE 'GMT')::date)",
            thread_id, identity.sec_type, identity.code, identity.name,
        )
    finally:
        await conn.close()


async def tick_step(
    state_id: int,
    status: str,
    *,
    note: Optional[str] = None,
    thread_id: str = "",
    node: Optional[str] = None,
    llm_context_id: Optional[str] = None,
) -> None:
    """One agent tick: in_progress stamps started_at; completed/failed
    stamp finished_at and store the tick note as the state's result
    context, keyed to the task (*state_id*, *node*) and the ticking
    conversation's ONE chained context (*llm_context_id* — the entry
    links that conversation's llm_requests rows). Unknown statuses raise
    (the caller validates)."""
    if status not in TICK_STATUSES:
        raise ValueError(f"tick status must be one of {TICK_STATUSES}, got {status!r}")
    conn = await _connect()
    try:
        result_context_id = None
        if note is not None:
            result_context_id = await _insert_context(
                conn, thread_id, "step-result",
                json.dumps({"note": str(note)[:_NOTE_MAX]},
                           ensure_ascii=False),
                node=node, state_id=state_id,
                llm_context_id=llm_context_id)
        if status == "in_progress":
            await conn.execute(
                "UPDATE text.deep_agent_states SET status = 'in_progress', "
                "started_at = COALESCE(started_at, now()) WHERE state_id = $1",
                state_id,
            )
        else:
            await conn.execute(
                "UPDATE text.deep_agent_states SET status = $2, "
                "finished_at = now(), "
                "result_context_id = COALESCE($3, result_context_id) "
                "WHERE state_id = $1",
                state_id, status, result_context_id,
            )
    finally:
        await conn.close()
    if thread_id:
        events.publish({
            "type": "state_updated", "thread_id": thread_id,
            "state_id": int(state_id), "status": status,
            "ts": events.now_iso(),
        })


async def create_child_state(
    thread_id: str, parent_state_id: Optional[int], node: str, title: str,
    *,
    llm_context_id: Optional[str] = None,
) -> int:
    """A spawned subagent's child state — already running under the
    step that was in_progress at spawn time (LangGraph's subgraph). The
    child mints its OWN llm_context_id (its conversation is not the
    parent node's); the registry item lands in the store when the
    output hook settles the child."""
    conn = await _connect()
    try:
        child_id = await conn.fetchval(
            "INSERT INTO text.deep_agent_states "
            "(thread_id, parent_state_id, node, title, status, ordinal, "
            "started_at, llm_context_id) "
            "VALUES ($1, $2, $3, $4, 'in_progress', 0, now(), $5) "
            "RETURNING state_id",
            thread_id, parent_state_id, node, title, llm_context_id,
        )
    finally:
        await conn.close()
    events.publish({
        "type": "state_created", "thread_id": thread_id,
        "state_id": int(child_id), "node": node, "title": title,
        "status": "in_progress", "ordinal": 0,
        "parent_state_id": parent_state_id, "ts": events.now_iso(),
    })
    return int(child_id)


async def stamp_states_context(
    state_ids: Sequence[int], llm_context_id: str,
) -> None:
    """Point plan-step states at their node's ONE context id — the
    execute node stamps its whole plan at entry (the agent loop walks
    the steps inside its single conversation). Idempotent: rows that
    already carry a context (a re-tick after a re-stamp) keep theirs."""
    if not state_ids:
        return
    conn = await _connect()
    try:
        await conn.execute(
            "UPDATE text.deep_agent_states SET llm_context_id = $2 "
            "WHERE state_id = ANY($1::bigint[]) AND llm_context_id IS NULL",
            [int(s) for s in state_ids], llm_context_id,
        )
    finally:
        await conn.close()


async def finish_child_state(
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
    conversation's llm_requests rows)."""
    if status not in ("completed", "failed"):
        raise ValueError(f"child status must be completed|failed, got {status!r}")
    conn = await _connect()
    try:
        payload = (
            {"answer": str(answer)[:_NOTE_MAX]} if answer is not None
            else {"error": str(error)[:_NOTE_MAX] if error else "failed"}
        )
        result_context_id = await _insert_context(
            conn, thread_id, "subagent-result",
            json.dumps(payload, ensure_ascii=False),
            node=node, state_id=state_id, llm_context_id=llm_context_id)
        await conn.execute(
            "UPDATE text.deep_agent_states SET status = $2, finished_at = now(), "
            "result_context_id = COALESCE($3, result_context_id), "
            "error = COALESCE($4, error) WHERE state_id = $1",
            state_id, status, result_context_id,
            str(error)[:_NOTE_MAX] if error else None,
        )
    finally:
        await conn.close()
    if thread_id:
        events.publish({
            "type": "state_updated", "thread_id": thread_id,
            "state_id": int(state_id), "status": status,
            "ts": events.now_iso(),
        })


async def record_final_result(
    thread_id: str, *,
    answer: Optional[str] = None,
    error: Optional[str] = None,
) -> Optional[int]:
    """The run's FINAL RESULT — the HARDCODED result node's fixed output:
    one context row of kind final-result whose payload is the run's
    answer document ({"answer": ...}, NOT truncated — it is the document
    the UI's Result view displays) or, when execute failed, its failure
    reason ({"error": ...}). Keyed to the result node (no state row, no
    chained context — the node does no LLM work). Returns the context
    id, or None when there was neither an answer nor an error to record
    (nothing written)."""
    payload: Optional[Dict[str, Any]] = None
    if answer:
        payload = {"answer": str(answer)}
    elif error:
        payload = {"error": str(error)[:_NOTE_MAX]}
    if payload is None:
        return None
    conn = await _connect()
    try:
        context_id = await _insert_context(
            conn, thread_id, "final-result",
            json.dumps(payload, ensure_ascii=False),
            node=RESULT_NODE)
    finally:
        await conn.close()
    events.publish({"type": "result_recorded", "thread_id": thread_id,
                    "ts": events.now_iso()})
    return int(context_id) if context_id is not None else None


async def finalize_thread(thread_id: str, *, failure: Optional[str] = None) -> None:
    """Settle whatever the run ended with: steps still in_progress are
    failed (the run died inside them / ended mid-step), still-pending
    steps are skipped (never reached). Children follow the same rule."""
    conn = await _connect()
    try:
        await conn.execute(
            "UPDATE text.deep_agent_states SET status = 'failed', "
            "finished_at = now(), error = $2 "
            "WHERE thread_id = $1 AND status = 'in_progress'",
            thread_id,
            (failure or "run ended inside this step")[:_NOTE_MAX],
        )
        await conn.execute(
            "UPDATE text.deep_agent_states SET status = 'skipped', "
            "finished_at = now(), error = 'run ended before this step started' "
            "WHERE thread_id = $1 AND status = 'pending'",
            thread_id,
        )
    finally:
        await conn.close()
    events.publish({"type": "states_settled", "thread_id": thread_id,
                    "ts": events.now_iso()})


async def list_thread_states(thread_id: str) -> List[StateRow]:
    """The thread's TOP-LEVEL states in plan order (children excluded —
    load them by parent_state_id when needed)."""
    conn = await _connect()
    try:
        records = await conn.fetch(
            f"SELECT {_STATE_COLUMNS} FROM text.deep_agent_states "
            f"WHERE thread_id = $1 AND parent_state_id IS NULL "
            f"ORDER BY ordinal, state_id",
            thread_id,
        )
    finally:
        await conn.close()
    return [_state_row(r) for r in records]


async def purge_thread(thread_id: str) -> None:
    """Remove one thread entirely (its llm_requests chains first — a
    request's chain_id is the node's llm_context_id, resolvable only
    while the linking rows exist — then states, whose self-FK cascades
    children and whose contexts.state_id CASCADE removes the
    task-linked context rows, then the thread's remaining contexts
    (final-result and unlinked rows), its identity link and its store
    items). The bridge's DELETE /api/ai/summary-runs/:thread_id and
    11_deep_agent_runs_prune.sql follow the same order."""
    from llm_agents.deep_agents.graph.state_store import delete_thread_items

    conn = await _connect()
    try:
        await conn.execute(
            "DELETE FROM text.llm_requests WHERE chain_id IN ("
            "SELECT s.llm_context_id FROM text.deep_agent_states s "
            "WHERE s.thread_id = $1 AND s.llm_context_id IS NOT NULL "
            "UNION SELECT i.value ->> 'llm_context_id' "
            "FROM text.deep_agent_store_items i "
            "WHERE i.namespace[1:2] = ARRAY['deep_agent', $1]::text[] "
            "AND i.value ->> 'llm_context_id' IS NOT NULL "
            "UNION SELECT i.key FROM text.deep_agent_store_items i "
            "WHERE i.namespace[1:3] = ARRAY['deep_agent', $1, 'contexts']::text[])",
            thread_id)
        await conn.execute(
            "DELETE FROM text.deep_agent_states WHERE thread_id = $1", thread_id)
        await conn.execute(
            "DELETE FROM text.deep_agent_contexts WHERE thread_id = $1", thread_id)
        await conn.execute(
            "DELETE FROM text.deep_agent_identities WHERE thread_id = $1", thread_id)
    finally:
        await conn.close()
    await delete_thread_items(thread_id)
