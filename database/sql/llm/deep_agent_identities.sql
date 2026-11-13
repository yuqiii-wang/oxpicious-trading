
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
