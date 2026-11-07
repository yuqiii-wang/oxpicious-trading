-- ============================================================================
--  Deep-agent thread state — LangGraph's concepts, this repo's storage
--  Tables: llm.deep_agent_states  (one row per node/task state of a run)
--          llm.deep_agent_contexts (the JSONB payloads states point into)
--          llm.deep_agent_identities (code <-> thread_id link, one row
--          per scoped run — the reverse index the summary history's
--          per-code read filters on)
--  Schema: langgraph (the checkpoint store of the LangGraph runtime)
--
--  One deep-agent run (one UI analysis-summary generation) = one CHAIN of
--  three LangGraph threads (2026-11-06 three-graph refactor): the PLAN
--  graph's thread (the ROOT — its own id is the run's super_thread_id),
--  the CUSTOM graph's thread (the on-demand step graph the plan builds)
--  and the RESULT graph's thread (the four-review wave + the run's final
--  answer) — chained by the super_thread_id column (every row of the
--  run's threads carries the root's id). Legacy runs are single-thread
--  and read as their own chain via COALESCE(super_thread_id, thread_id).
--  The runner (llm_agents/deep_agents/langgraph/runner.py) orchestrates
--  the three StateGraphs sequentially — plan -> custom (waits) -> result:
--
--    LangGraph concept      | here
--    -----------------------+---------------------------------------------
--    thread (thread_id)     | thread_id TEXT (uuid hex) on every row —
--                           | the parent id that governs the run's nodes
--                           | AND the graph config's thread_id (the
--                           | checkpointer keys the run's checkpoints
--                           | by the same value)
--    node                   | the `node` column — the graph node a state
--                           | belongs to (a planned step's work tag, or
--                           | the runner's built-ins: spawn_subagent)
--    task (node execution)  | the state ROW itself: status walks
--                           | pending -> in_progress -> completed/failed,
--                           | with started_at/finished_at (deep agents'
--                           | todo-list lifecycle, one in_progress max)
--    state / checkpoint     | the JSONB payloads in deep_agent_contexts
--                           | (plan JSON, step notes, tick notes, the
--                           | final-result document) PLUS the graph
--                           | state snapshots per superstep in
--                           | langgraph.checkpoints (goal/data/plan/
--                           | result — interrupt/resume groundwork)
--    chained context        | each context row carries llm_context_id —
--                           | the ONE context (uuid hex) of the node
--                           | execution whose conversation produced it
--                           | (the same key as states.llm_context_id and
--                           | llm_requests.chain_id: one node, one
--                           | chained context, its entries link the
--                           | node's LLM requests)
--    subgraph               | parent_state_id (self FK): a spawned
--                           | subagent run is a child state under the
--                           | step that was in_progress at spawn time
--    interrupt / resume     | groundwork only: checkpoints persist, but
--                           | run_thread always starts a fresh thread
--
--  The planned steps come from the LLM itself: the plan node — the
--  HARDCODED first node of EVERY run — asks the model for a plan JSON
--    {"steps": [{"node": "trend_structure", "title": "...", "notes": "...",
--                "delegate": {"goal": "..."}}]}
--  (validated + repaired by llm_agents/deep_agents/planning/plan_json.py; the
--  request's plan prompts of llm.ai_prompts, prompt_type 2, are
--  optional flavor — without them the built-in planner instruction
--  lays the plan out) and inserts one pending state per step; the
--  execute node then hands the plan to the agent with the
--  update_plan_step / update_plan tools.
--
--  The plan is a LIVING graph with a MAIN LANE and FORKED PATHS:
--    * a step carrying a "delegate" goal is a FORK: the agent runs it
--      by spawning a BACKGROUND subagent (spawn_subagent
--      background=true, gathered later via collect_subagents) — the
--      child state under the step is the forked path;
--    * every agent on the run may EXTEND the plan when its work hits
--      something needing further investigation: the main thread's
--      update_plan adds / revises / removes steps that have NOT run
--      (pending only; removed steps settle skipped with the removal
--      reason) and TERMINATES running forks (cancel_run_ids /
--      cancel_running — the fork's child state settles failed
--      'cancelled by plan update');
--    * a subagent's plan kit (mounted by the runner's input hook,
--      plan_tools.subagent_plan_tools) is scoped to its OWN fork: it
--      may add child states under its fork state and tick only those
--      (addressed by state id) — never another node or agent;
--    * the main thread's ticks address the CURRENT top-level rows by
--      ordinal (a fresh read per tick — runtime adds can insert
--      mid-plan, add_plan_states shifting later rows' ordinals).
--  Steps still pending/in_progress when the run ends are settled by the
--  finalize node (skipped / failed).
--
--  The result node is HARDCODED (never model-planned): the graph's LAST
--  node — after finalize settles — it writes the run's final answer (or
--  its failure reason) as ONE context row of kind 'final-result' — its
--  fixed node output, the document the UI's Result view displays for
--  the thread. A run stopped before execute finished never reaches it
--  (no final-result row).
--
--  Vocabulary columns: `kind` is a CONVERTED label column
--  (static.label_dim domain 'agent_context_kind' — see the column
--  comment); `status` / `node` stay TEXT on purpose — writer-controlled
--  lifecycle and model-proposed open-ended tags (the llm.* LLM-layer
--  exception, see 01_label_dim.sql's header). `status` is
--  writer-controlled (plan_store), not model-controlled.
--
--  Purge order: the thread's llm.llm_requests chains first (a
--  request's chain_id is the node's llm_context_id — resolvable from
--  states.llm_context_id + the store's node items + the context
--  registry while they exist), then states (the self-FK cascades
--  children, and contexts.state_id CASCADE removes their task-linked
--  context rows), then the thread's remaining contexts (kind
--  final-result and unlinked legacy rows), then the identity rows —
--  DELETE ... WHERE thread_id = $1 on each (the bridge's DELETE
--  /api/ai/summary-runs/:thread_id and 09_deep_agent_runs_prune.sql).
--  The langgraph.checkpoint rows (same thread_id) are framework-
--  managed; purge them by thread_id too when retiring a thread.
--
--  Applied after 06_ai_prompts.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm.deep_agent_contexts (
    context_id     BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id      TEXT        NOT NULL,  -- the run this blob belongs to (uuid hex)
    kind           SMALLINT    NOT NULL,  -- static.label_dim 'agent_context_kind'
    payload        JSONB       NOT NULL,  -- the plan JSON / step notes / result notes
    node           TEXT,                  -- the graph node / task the entry belongs to (see the column COMMENT below)
    state_id       BIGINT,                -- the task/step state the entry belongs to; the FK to deep_agent_states is added below (each table references the other, so it cannot live in either CREATE)
    llm_context_id TEXT,                  -- the ONE chained LLM context of the node execution whose conversation produced the entry (see the column COMMENT below)
    is_critical    BOOLEAN     NOT NULL DEFAULT false,
                     -- set when the LLM response payload that produced the
                     -- entry contained a CRITICAL DISCOVERY (see the column
                     -- COMMENT below)
    tags           TSVECTOR,  -- StructBERT-tokenized search tags over the payload's text (writer-built — see the column COMMENT below)
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS llm.deep_agent_states (
    state_id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id        TEXT        NOT NULL,  -- parent id governing this run's nodes
    super_thread_id  TEXT,                  -- the CHAIN id: the run's plan-graph (root) thread id — one run = plan/custom/result threads sharing it (NULL = legacy single-thread runs)
    parent_state_id  BIGINT      REFERENCES llm.deep_agent_states (state_id) ON DELETE CASCADE,
                     -- NULL = a top-level plan step; set = a spawned
                     -- subagent's child state (the LangGraph subgraph case)
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

-- The context <-> requests join: a context entry's chained conversation
-- → its llm.llm_requests rows (mirrors ix_llm_requests_chain_id).
CREATE INDEX IF NOT EXISTS ix_deep_agent_contexts_chain
    ON llm.deep_agent_contexts (llm_context_id)
    WHERE llm_context_id IS NOT NULL;

-- FK-cascade support: deleting a state row must find its task-linked
-- context entries without a seq scan (PG does not auto-index FK sides).
CREATE INDEX IF NOT EXISTS ix_deep_agent_contexts_state
    ON llm.deep_agent_contexts (state_id)
    WHERE state_id IS NOT NULL;

-- Full-text search over the contexts' PAYLOAD TEXT: the tags column
-- (created with the table above) and its GIN index. Searches are
-- THREAD-SCOPED — the query filters thread_id (the run chain's member
-- ids) beside the tsvector match, the same shape as
-- ix_llm_requests_search_tsv_gin + the chain_id filter on
-- llm.llm_requests. Query side (segmented the same way):
-- tags @@ websearch_to_tsquery('simple', tokenize_for_search(<q>)).
CREATE INDEX IF NOT EXISTS ix_deep_agent_contexts_tags_gin
    ON llm.deep_agent_contexts USING gin (tags);

-- The CRITICAL lane: a run's flagged critical discoveries, newest
-- first, scoped by thread (the chain members) — the must-check read
-- the step/review prompts teach (search_thread_context
-- critical_only=true resolves through it after the tags match).
CREATE INDEX IF NOT EXISTS ix_deep_agent_contexts_critical
    ON llm.deep_agent_contexts (thread_id, context_id DESC)
    WHERE is_critical;

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
    'FORKED PATH), or the fork state for nodes the subagent itself '
    'added under its fork (LangGraph''s subgraph-as-node case). '
    'CASCADE: deleting a parent removes its children.';

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
    'failed, the run died inside it, or a running fork was terminated '
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
    'step is a fork). Load it instead of re-deriving the step from the '
    'plan JSON blob.';

COMMENT ON COLUMN llm.deep_agent_states.result_context_id IS
    'Pointer to the step''s result context row (the tick note — what the '
    'step confirmed, pass/fail + the numbers; a subagent''s final answer '
    'for child states). Written by the tick/finalize that closed the '
    'state.';

COMMENT ON TABLE llm.deep_agent_contexts IS
    'Deep-agent thread contexts: the JSONB payloads deep_agent_states '
    'point into by id — the thread''s plan JSON (kind plan), each step''s '
    'input notes (kind step-input), its tick note (kind step-result), a '
    'spawned subagent''s final answer (kind subagent-result), the '
    'supervisor node''s main-path review (kind supervisor-review — the '
    'bounded cross-check of the walked steps'' findings) and the '
    'runner''s HARDCODED result node''s fixed output (kind final-result '
    '— the run''s final answer document, the UI Result view''s display '
    'source; written right after the step lane, absent on stopped runs). '
    'Granularity: ONE ENTRY per thread per node per task/step — or per '
    'one LLM request for single-call task types (the compact node) — '
    'keyed by node / state_id / llm_context_id. CONTEXT HYGIENE: '
    'payloads carry the IMPORTANT data only (the plan as validated, '
    'what a step must confirm / confirmed, condensed answers — writers '
    'cap notes), never internal thinking: no message lists, no system '
    'prompts, no tool-call transcripts, no reasoning traces. '
    'LangGraph''s checkpoint/store payloads without the framework: '
    'states stay small and linkable, blobs stay queryable (JSONB).';

COMMENT ON COLUMN llm.deep_agent_contexts.node IS
    'The graph node / task the entry belongs to: the step''s business '
    'tag (states.node) for step-input/step-result rows, the fork slug '
    '(fork_<topic>) for subagent-result rows, and the runner-lane tags '
    'plan | compact | result for the plan, compaction and final-result '
    'entries. TEXT on purpose: model-proposed business tags are '
    'open-ended. NULL on rows written before the column existed.';

COMMENT ON COLUMN llm.deep_agent_contexts.state_id IS
    'The deep_agent_states row of the task/step the entry belongs to '
    '(the step''s state for its input/result notes, the fork''s child '
    'state for a subagent answer, the running planning row for the plan '
    'JSON). CASCADE: a deleted state takes its task-linked entries with '
    'it (purge deletes states first). NULL for the final-result entry '
    '(the hardcoded result node keeps no state row) and legacy rows.';

COMMENT ON COLUMN llm.deep_agent_contexts.llm_context_id IS
    'The ONE chained LLM context (uuid hex) of the node execution whose '
    'conversation PRODUCED the entry — the same key as '
    'deep_agent_states.llm_context_id, the store''s context-registry '
    'item key and llm.llm_requests.chain_id: one node, one context, '
    'and the entry links that conversation''s requests (join '
    'llm_requests.chain_id). Step inputs minted at plan time carry the '
    'PLAN node''s context (its conversation proposed them). NULL when '
    'no LLM work produced the entry (kind final-result), on '
    'degraded-path writes, and on rows written before the column '
    'existed.';

COMMENT ON COLUMN llm.deep_agent_contexts.kind IS
    'Which payload the row carries, as the static.label_dim '
    '''agent_context_kind'' code: 0 plan (the thread''s plan JSON as '
    'proposed by the LLM) | 1 step-input (a step''s `notes` — what it '
    'must confirm) | 2 step-result (the tick note — what it confirmed) '
    '| 3 subagent-result (a child state''s final answer) | 4 '
    'final-result (the hardcoded result node''s fixed output — the '
    'run''s final answer document) | 5 supervisor-review (the '
    'SUPERVISOR WATCHDOG''s intervention — the independent per-run '
    'agent beside the graph wakes only when a root-path node failed; '
    'the bounded diagnosis + plan/topology verdict the UI''s Thread '
    'Graph shows beneath the canvas by default). Closed vocabulary: '
    'new payload kinds are one INSERT in the three label mirrors; '
    'decode joins label_dim on (domain, code).';

COMMENT ON COLUMN llm.deep_agent_contexts.is_critical IS
    'TRUE when the LLM response payload that produced this entry '
    'contained a CRITICAL DISCOVERY — a finding that changes the run''s '
    'conclusion (a material break in the evidence, a risk/trigger the '
    'plan must react to, a number that overturns the working view). '
    'Set STRUCTURALLY by the writer (planning/store.py::_insert_context '
    'lifts the payload''s "critical": true flag into the column; the '
    'flag arrives from the model two ways — the fork tick tool''s '
    'explicit critical argument, or the [CRITICAL] final reply line '
    'the step/fork/output prompts teach, parsed fail-soft out of the '
    'raw reply before the payload is built). The must-check lane: '
    'later steps, the reviews and the final answer weigh every flagged '
    'row (search_thread_context critical_only=true / the partial '
    'ix_deep_agent_contexts_critical index).';

COMMENT ON COLUMN llm.deep_agent_contexts.tags IS
    'Search tags over the entry''s payload text — every JSON string '
    'leaf of the payload, Chinese pre-segmented by the repo''s '
    'StructBERT segmenter (_common.tokenization, the same contract as '
    'llm.llm_requests.search_tsv), space-joined and built in the '
    'INSERT by the writer as strip(to_tsvector(''simple'', <tokens>)) '
    '— never a GENERATED column (''simple'' does not segment CJK), '
    'positions stripped (GIN indexes lexemes only). NULL = not yet '
    'tokenized (tokenizer outage at write time; healed by '
    'planning/store.backfill_context_tags_async, which the '
    'thread-internal search runs scoped to the run chain before '
    'searching). GIN-indexed (ix_deep_agent_contexts_tags_gin) and '
    'queried THREAD-SCOPED: tags @@ websearch_to_tsquery(''simple'', '
    'tokenize_for_search(<q>)) AND thread_id = ANY(<the run chain''s '
    'member ids>) — the deep agents'' search_thread_context.';

-- ============================================================================
--  Thread identities — the CODE <-> THREAD_ID link. deep_agent_states
--  answers "what did thread X do"; this table is the REVERSE index —
--  "which threads were about code C" — so the summary history's
--  classification-nav-filtered read is one indexed lookup, not a scan of
--  states/contexts. One row per SCOPED run: run_thread inserts it at RUN
--  START from the request's scope pick (the classification-nav selection
--  that focused the summary: {sec_type, code} for a security or
--  {sec_type: industry, code: industry_id} for an industry entity) —
--  every scoped generate sets the link whether or not the plan phase
--  persists (rows whose thread never planned simply never join to
--  states). Market-wide runs (no scope pick) create NO row — a row means
--  "this run was about this subject".
--
--  sec_type is a CONVERTED label column (static.label_dim domain
--  'sec_type': 0 etf | 1 index | 2 stock | 3 industry — the third
--  llm.* LLM-layer table converted; see 01_label_dim.sql's header).
--  `date` is the run's GMT operational day (a new day starts at Beijing
--  08:00 — the same boundary as llm.llm_requests.date and the
--  summary-runs read's day bucket), stamped at insert from now().
--
--  Purge: DELETE ... WHERE thread_id = $1 alongside the states/contexts
--  deletes (plan_store.purge_thread).
-- ============================================================================
CREATE TABLE IF NOT EXISTS llm.deep_agent_identities (
    identity_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id    TEXT        NOT NULL,  -- the scoped run (deep_agent_states)
    sec_type     SMALLINT    NOT NULL,  -- static.label_dim 'sec_type' code
    code         TEXT        NOT NULL,  -- security code (index/etf/stock) or industry id
    name         TEXT,                  -- the pick's display name when carried
    date         DATE        NOT NULL,  -- GMT operational day of the run
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The per-code read: "runs about this subject on/around day D" — the
-- summary-runs endpoint's nav-filtered query shape.
CREATE INDEX IF NOT EXISTS ix_deep_agent_identities_code
    ON llm.deep_agent_identities (sec_type, code, date, thread_id);

-- The per-thread reverse lookup (the unfiltered rows join + the purge).
CREATE INDEX IF NOT EXISTS ix_deep_agent_identities_thread
    ON llm.deep_agent_identities (thread_id);

COMMENT ON TABLE llm.deep_agent_identities IS
    'Deep-agent thread identities: the code <-> thread_id link — one row '
    'per SCOPED deep-agent run, inserted by run_thread at RUN START from '
    'the classification-nav scope pick that focused the summary (every '
    'scoped generate sets the link; plan-less runs'' rows simply never '
    'join to states). The reverse index over deep_agent_states: given a '
    'security code or industry id, the thread ids that ran about it (the '
    'summary history''s per-code filter), one indexed lookup instead of a '
    'states/contexts scan. Market-wide runs create no row.';

COMMENT ON COLUMN llm.deep_agent_identities.thread_id IS
    'The scoped run''s thread id (uuid hex) — the llm.deep_agent_states '
    'parent id the subject links to.';

COMMENT ON COLUMN llm.deep_agent_identities.sec_type IS
    'The subject''s security class as the static.label_dim ''sec_type'' '
    'code: 0 etf | 1 index | 2 stock | 3 industry (decode joins '
    'label_dim on (domain, code)).';

COMMENT ON COLUMN llm.deep_agent_identities.code IS
    'The scope pick''s subject id: the security code for sec_type '
    'index/etf/stock, the industry id (e.g. BANKS) for sec_type '
    'industry.';

COMMENT ON COLUMN llm.deep_agent_identities.name IS
    'The pick''s display name when the request carried one (prompt '
    'context; the UI''s badge text).';

COMMENT ON COLUMN llm.deep_agent_identities.date IS
    'The run''s GMT operational day (a new day starts at Beijing 08:00) '
    '— stamped at insert; the day bucket the filtered history groups '
    'by, the same boundary as llm.llm_requests.date.';

-- ============================================================================
--  The LangGraph runtime's checkpoint store. The runner compiles its
--  StateGraph with AsyncPostgresSaver; the saver connects with
--  search_path=langgraph so its UNQUALIFIED DDL (CREATE TABLE IF NOT
--  EXISTS checkpoints / checkpoint_blobs / checkpoint_writes /
--  checkpoint_migrations, run by the saver's setup()) lands in this
--  dedicated schema instead of polluting public. The schema itself is
--  created SQL-first here; the tables inside are framework-managed —
--  do not hand-edit them, and let setup() evolve them on upgrade.
--
--  One checkpoint per superstep, keyed by the run's thread_id (the same
--  uuid hex as llm.deep_agent_states.thread_id): the graph state
--  snapshot (goal / structured data / the persisted plan / the answer).
--  llm.deep_agent_states remains the task-level source of truth the UI
--  reads; checkpoints are the runtime-level memory (resume groundwork).
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS langgraph;

COMMENT ON SCHEMA langgraph IS
    'LangGraph checkpoint store (framework-managed): checkpoints / '
    'checkpoint_blobs / checkpoint_writes / checkpoint_migrations are '
    'created and evolved by langgraph-checkpoint-postgres '
    'AsyncPostgresSaver.setup() with search_path=langgraph. Keyed by '
    'the deep-agent run thread_id — one checkpoint per superstep of the '
    'runner''s plan -> execute -> finalize graph; see '
    'database/sql/text/llm/07_deep_agent_states.sql.';

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

-- The contexts join carries a kind filter on every read path (the
-- summary-history day read's plan-goal lookup, the thread snapshot's
-- plan / final-result reads): (thread_id, kind) serves every
-- thread-scoped read, tighter than a bare (thread_id) index.
CREATE INDEX IF NOT EXISTS ix_deep_agent_contexts_thread_kind
    ON llm.deep_agent_contexts (thread_id, kind);
