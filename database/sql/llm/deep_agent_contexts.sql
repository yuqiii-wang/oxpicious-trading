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
--  The plan is a LIVING graph with a MAIN LANE and SPAWNED PATHS:
--    * a step carrying a "delegate" goal is a SPAWN: the agent runs it
--      by spawning a BACKGROUND subagent (spawn_subagent
--      background=true, gathered later via collect_subagents) — the
--      child state under the step is the spawned path;
--    * every agent on the run may EXTEND the plan when its work hits
--      something needing further investigation: the main thread's
--      update_plan adds / revises / removes steps that have NOT run
--      (pending only; removed steps settle skipped with the removal
--      reason) and TERMINATES running spawns (cancel_run_ids /
--      cancel_running — the spawn's child state settles failed
--      'cancelled by plan update');
--    * a subagent's plan kit (mounted by the runner's input hook,
--      plan_tools.subagent_plan_tools) is scoped to its OWN spawn: it
--      may add child states under its spawn state and tick only those
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
    'tag (states.node) for step-input/step-result rows, the spawn slug '
    '(spawn_<topic>) for subagent-result rows, and the runner-lane tags '
    'plan | compact | result for the plan, compaction and final-result '
    'entries. TEXT on purpose: model-proposed business tags are '
    'open-ended. NULL on rows written before the column existed.';


COMMENT ON COLUMN llm.deep_agent_contexts.state_id IS
    'The deep_agent_states row of the task/step the entry belongs to '
    '(the step''s state for its input/result notes, the spawn''s child '
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
    'flag arrives from the model two ways — the spawn tick tool''s '
    'explicit critical argument, or the [CRITICAL] final reply line '
    'the step/spawn/output prompts teach, parsed fail-soft out of the '
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


-- The contexts join carries a kind filter on every read path (the
-- summary-history day read's plan-goal lookup, the thread snapshot's
-- plan / final-result reads): (thread_id, kind) serves every
-- thread-scoped read, tighter than a bare (thread_id) index.
CREATE INDEX IF NOT EXISTS ix_deep_agent_contexts_thread_kind
    ON llm.deep_agent_contexts (thread_id, kind);


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
