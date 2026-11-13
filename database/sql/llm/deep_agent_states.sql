
CREATE TABLE IF NOT EXISTS llm.deep_agent_states (
    state_id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id        TEXT        NOT NULL,  -- parent id governing this run's nodes
    super_thread_id  TEXT,                  -- the CHAIN id: the run's plan-graph (root) thread id — one run = plan/custom/result threads sharing it (NULL = legacy single-thread runs)
    parent_state_id  BIGINT      REFERENCES llm.deep_agent_states (state_id) ON DELETE CASCADE,
                     -- NULL = a top-level plan step; set = a spawned
                     -- subagent's child state (the LangGraph subgraph case)
    child_kind       SMALLINT,   -- the child KIND (agent_child_kind label
                     -- domain: 0 spawn | 1 fork) — how the child was
                     -- raised: spawn = a FRESH-context sub node (goal +
                     -- business context only), fork = an INHERIT-context
                     -- sub node (continues a deep copy of the parent's
                     -- transcript). NULL = a top-level row (not a child).
    node             TEXT        NOT NULL,  -- graph node the state belongs to (work tag)
    title            TEXT        NOT NULL,  -- the step as the plan stated it
    status           TEXT        NOT NULL DEFAULT 'pending',
                     -- pending | in_progress | completed | failed | skipped
    ordinal          SMALLINT    NOT NULL DEFAULT 0,
                     -- plan order of top-level steps (children: 0, order
                     -- among siblings is state_id)
    input_context_id  BIGINT     REFERENCES llm.deep_agent_contexts (context_id) ON DELETE CASCADE,
    result_context_id BIGINT     REFERENCES llm.deep_agent_contexts (context_id) ON DELETE CASCADE,
    llm_context_id   TEXT,                  -- the ONE LLM context the state ran under — a REFERENCE ONLY (the context data itself is never persisted; see 10_deep_agent_store.sql)
    error            TEXT,                  -- failure reason (failed/skipped settle)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,           -- first in_progress tick
    finished_at      TIMESTAMPTZ            -- completed/failed/skipped settle
);

-- The thread's plan, in order (the UI/response snapshot's read).
CREATE INDEX IF NOT EXISTS ix_deep_agent_states_thread_ordinal
    ON llm.deep_agent_states (thread_id, ordinal, state_id);

-- Live progress reads (which steps are still open in this thread).
CREATE INDEX IF NOT EXISTS ix_deep_agent_states_thread_status
    ON llm.deep_agent_states (thread_id, status);

-- The super-chain read: every state of one run's three chained graphs
-- (plan / custom / result — the root thread's id stamps all of them;
-- legacy single-thread runs read as their own chain via
-- COALESCE(super_thread_id, thread_id)).
CREATE INDEX IF NOT EXISTS ix_deep_agent_states_super
    ON llm.deep_agent_states (super_thread_id)
    WHERE super_thread_id IS NOT NULL;

-- ONE NODE, ONE CHAINED CONTEXT: a node execution's ONE LLM conversation
-- has ONE uuid-hex context id (state_store.NodeRunWriter mints it;
-- subagent children mint their own); the context ENTRY rows that
-- conversation produced carry the same key, so the payloads chain under
-- their node's one context and link its LLM requests
-- (llm.llm_requests.chain_id = the same uuid). Granularity: one context
-- entry = one payload per thread per node per task/step — or per ONE LLM
-- request when the task type is a single-call node (the compact node's
-- merge call).
--
-- The contexts <-> states FK cannot live in either CREATE: each table
-- references the other (states.input_context_id / result_context_id ->
-- contexts above; contexts.state_id -> states here), so the reverse side
-- lands once both tables exist. Guarded so re-applies no-op — the live
-- DB's constraint was added by the former ADD COLUMN migration under
-- PG's auto name.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'llm.deep_agent_contexts'::regclass
          AND confrelid = 'llm.deep_agent_states'::regclass
          AND contype = 'f'
    ) THEN
        ALTER TABLE llm.deep_agent_contexts
            ADD CONSTRAINT fk_deep_agent_contexts_state
            FOREIGN KEY (state_id) REFERENCES llm.deep_agent_states (state_id)
            ON DELETE CASCADE;
    END IF;
END $$;

COMMENT ON TABLE llm.deep_agent_states IS
    'Deep-agent thread state: one row per graph node/task state of one '
    'deep-agent run (the thread). thread_id is the parent id governing '
    'the run''s nodes; planned steps arrive as the LLM''s plan JSON (the '
    'plan prompts of llm.ai_prompts, prompt_type 2), inserted as pending '
    'rows by the runner''s plan node and TICKED by the agent via its '
    'update_plan_step tool as it finishes each task; the finalize node '
    'settles whatever the run ended with. States carry only lifecycle '
    '(status/timestamps) + id pointers into llm.deep_agent_contexts — '
    'the payloads (plan JSON, step input/result notes) live there. '
    'LangGraph''s thread/node/task/state/subgraph concepts without the '
    'LangGraph framework; see llm_agents/deep_agents/langgraph/runner.py.';

COMMENT ON COLUMN llm.deep_agent_states.thread_id IS
    'The run''s thread id (uuid hex) — the parent id that governs every '
    'state of the run, the same scoping unit LangGraph checkpoints '
    'under. One UI analysis-summary generation = one thread.';

COMMENT ON COLUMN llm.deep_agent_states.super_thread_id IS
    'The CHAIN id — one run''s three chained LangGraphs (2026-11-06 '
    'three-graph refactor) each own a thread: plan (the root thread, '
    'whose own id IS the chain id and stamps its rows with it), custom '
    '(the on-demand step graph) and result (the four-review-wave graph). '
    'Every row of every thread of the run carries the SAME value = the '
    'root thread''s id; the result graph''s builder finds the custom '
    'graph''s output and the UI renders the whole chain through it. '
    'NULL = legacy single-thread runs (read as their own chain via '
    'COALESCE(super_thread_id, thread_id)). No FK: the root thread''s '
    'rows are deleted with the chain, not referenced.';

COMMENT ON COLUMN llm.deep_agent_states.parent_state_id IS
    'NULL for a top-level plan step (the MAIN LANE); the in-progress '
    'step''s state_id for a spawned subagent''s child state (its '
    'SPAWNED PATH), or the spawn state for nodes the subagent itself '
    'added under its spawn (LangGraph''s subgraph-as-node case). '
    'CASCADE: deleting a parent removes its children.';

COMMENT ON COLUMN llm.deep_agent_states.child_kind IS
    'The child KIND (static.label_dim domain agent_child_kind): 0 = '
    'spawn — a FRESH-context sub node (it receives the goal plus the '
    'run''s business context, never the parent transcript); 1 = fork '
    '(2026-11-13) — an INHERIT-context sub node that continues a deep '
    'copy of its parent''s PRE-DRIFT transcript from a jev-chosen '
    'checkpoint plus a fork directive, the parent awaiting its '
    'handback; 2 = abandoned — the drift fork''s ABANDONED pre-fork '
    'tail stub (the drifted segment after the fork point, settled '
    'skipped with the drift note; the canvas demotes it to the '
    'right-side edge path beside the forked continuation). NULL for '
    'top-level rows (not children) and for child rows predating the '
    'column (read as spawn).';

COMMENT ON COLUMN llm.deep_agent_states.node IS
    'The graph node the state belongs to — the plan step''s work tag, a '
    'BUSINESS-aspect tag naming what the step settles about the subject '
    '(open vocabulary, model-proposed: trend_structure / momentum_state / '
    'valuation_level / signal_consensus / ...) or '
    'spawn_subagent for child states. TEXT: model-proposed tags are '
    'open-ended.';

COMMENT ON COLUMN llm.deep_agent_states.status IS
    'The task lifecycle: pending (planned, not started) | in_progress '
    '(the agent started it) | completed | failed (the step''s checks '
    'failed, the run died inside it, or a running spawn was terminated '
    'by a plan update) | skipped (the run ended before reaching it, or '
    'the step was removed by update_plan before it started). '
    'Writer-controlled (plan_store / plan_tools); the agents'' tick '
    'and update tools accept only the legal transitions.';

COMMENT ON COLUMN llm.deep_agent_states.ordinal IS
    '1-based plan order of top-level steps; child states carry 0 (their '
    'order is state_id).';

COMMENT ON COLUMN llm.deep_agent_states.input_context_id IS
    'Pointer to the step''s input context row (the plan step''s `notes` '
    '— what the step must confirm — plus its `delegate_goal` when the '
    'step is a spawn). Load it instead of re-deriving the step from the '
    'plan JSON blob.';

COMMENT ON COLUMN llm.deep_agent_states.result_context_id IS
    'Pointer to the step''s result context row (the tick note — what the '
    'step confirmed, pass/fail + the numbers; a subagent''s final answer '
    'for child states). Written by the tick/finalize that closed the '
    'state.';

-- ============================================================================
--  Deep-agent LangGraph STORE — BaseStore semantics over this repo's storage
--  Tables: llm.deep_agent_store_items (the store: namespace[] + key + JSONB)
--          plus deep_agent_states.llm_context_id — defined in
--          07_deep_agent_states.sql's CREATE TABLE (states reference their
--          LLM context BY ID — the context data itself is never persisted;
--          the column's COMMENT below)
--
--  The runner (llm_agents/deep_agents/langgraph/runner.py) already compiles its
--  StateGraph with the AsyncPostgresSaver CHECKPOINTER (per-superstep state
--  snapshots in the framework-managed langgraph schema, 07_deep_agent_states.sql). This file adds the other LangGraph persistence primitive,
--  the STORE (langgraph.store.base.BaseStore — cross-thread namespace/key
--  memory), implemented by llm_agents/deep_agents/langgraph/state_store.py as
--  DeepAgentStore: the langgraph-checkpoint-postgres 3.x line (verified
--  through 3.2.0) has no AsyncPostgresStore, so the store is repo-
--  implemented over SQL-first
--  tables and hooked into the graph via compile(store=...) — the framework
--  routes every put/get/search/delete through BaseStore.abatch.
--
--  PERSISTENCE POLICY (the reason the store exists in this shape):
--    * ONLY STATES are persisted. A store item is a node-execution state
--      (lifecycle status, timestamps, counts) or a context REFERENCE.
--    * LLM CONTEXT DATA IS NEVER PERSISTED — no message lists, no system
--      prompts, no tool-call transcripts, no answers' provenance blobs.
--      The runner's ThreadState likewise carries counts, not transcripts.
--    * CONTEXTS ARE REFERENCED BY ID: every LLM context (one node's single
--      running conversation) gets a uuid-hex id, and the store keeps a
--      CONTEXT REGISTRY (id -> node, the langgraph state/checkpoint id it
--      ran under, call counts) with zero message payload, and every
--      llm.deep_agent_states row gains llm_context_id so each persisted
--      state points at the context that produced it.
--    * ONE NODE, ONE LLM CONTEXT: the runner mints exactly one context id
--      per graph-node execution and threads it through the node's engine —
--      plan steps share the execute node's context (the agent loop walks
--      them inside its ONE conversation). Each spawned subagent child
--      mints its own. Finalize (no LLM) carries none.
--
--  Namespace layout (LangGraph hierarchical namespaces. The root label
--  must not be "langgraph" — BaseStore._validate_namespace):
--    ("deep_agent", <thread_id>)            keys node:plan | node:execute |
--                                           node:finalize — the runner-lane
--                                           node states the ThreadGraph UI
--                                           used to derive client-side
--    ("deep_agent", <thread_id>, "contexts") key <llm_context_id> — the
--                                           context registry (references +
--                                           counts only, no data)
--
--  Purge order (with 09's llm_requests→states→contexts→identities):
--  also delete the thread's store items — namespace[1:2] contains the
--  thread id (or the per-prefix delete in plan_store.purge_thread).
--  09_deep_agent_runs_prune.sql deletes them alongside the checkpoint
--  tables, AFTER resolving the thread's llm_requests chains from the
--  items (chain_id = the node's llm_context_id — node items' value and
--  the context registry's keys) and deleting those request rows first.
--
--  Applied after 07_deep_agent_states.sql (needs llm.deep_agent_states).
-- ============================================================================

COMMENT ON COLUMN llm.deep_agent_states.llm_context_id IS
    'The id of the LLM context this state ran under (uuid hex) — a '
    'REFERENCE ONLY: the context registry item (namespace ("deep_agent", '
    'thread_id, "contexts"), same key) holds the node, the langgraph '
    'checkpoint id and call counts, never the messages. Top-level plan '
    'steps share the execute node''s context id (the agent loop walks '
    'them inside its one conversation — one node one LLM context). '
    'spawned-subagent children each carry their own. NULL when the run '
    'predates the store or the node ran no LLM work.';


-- ============================================================================
--  Date-bucket reads on llm.deep_agent_states + thread+kind reads on
--  llm.deep_agent_contexts — the Summary History strip's two-phase load.
--
--  The strip's DATES read (GET /api/ai/summary-runs/dates) groups every
--  state row by the GMT operational day and the picked day's read
--  (GET /api/ai/summary-runs?date=) filters on ONE day; both shapes
--  previously seq-scanned the table — no index led on created_at, only
--  the thread-scoped (thread_id, ...) ones existed. The expression is
--  the queries' exact run_date bucket (immutable: a constant zone);
--  thread_id + status ride as trailing KEY columns so the per-date
--  aggregates (COUNT DISTINCT thread_id + status FILTER counts) are
--  index-only scans, and a day-equality predicate range-scans one day.
-- ============================================================================
CREATE INDEX IF NOT EXISTS ix_deep_agent_states_gmt_day
    ON llm.deep_agent_states (
        ((created_at AT TIME ZONE 'GMT')::date),
        thread_id,
        status
    );
