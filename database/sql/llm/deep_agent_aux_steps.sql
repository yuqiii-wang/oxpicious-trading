-- ============================================================================
--  Deep-agent AUX steps — the run's off-context work as first-class steps
--  Table: llm.deep_agent_aux_steps
--
--  description: A deep-agent run's auxiliary steps — the System One (jev) decision reads and the Chinese tokenization operations — recorded for UI display, never loaded into an LLM conversation.
--  tags: agent steps, 智能体步骤, decision, 决策, System One, jev, tokenization, 分词
--
--  ONE RUN'S AUXILIARY WORK, VISIBLE BUT NEVER IN CONTEXT: the harness
--  runs two kinds of work beside every LLM conversation that the model
--  never sees — (a) the SYSTEM ONE DECISION reads (the injected
--  JevDecisionEngine: resume / spawn / route-spawn / route-join /
--  failure-probe / kg-relevance / content-classification judgments
--  answered by the resident local-model host's onejev model over
--  POST /v1/systemone, ~0.1s each) and (b) the TOKENIZATION operations
--  (the StructBERT segmenter: a request-log row's search_tsv vector, a
--  context row's tags vector, a search query's pre-segmentation). Their
--  OUTPUTS are deliberately excluded from every LLM context — the
--  verdict payloads and tokenized text live HERE and on the UI only.
--
--  JOIN CONTRACT: the UI's steps view
--  (sql_views data_viz/api/services/ai-thread/state_agent_steps.sql)
--  joins these rows onto a task state by llm_context_id = the state's
--  llm_context_id (the ambient request chain the op ran under — the same
--  join the inference branch uses, with the same +/-2s tick window for
--  top-level steps that share the execute node's context). step_type
--  uses the agent_step_type label domain: 2 decision | 3 tokenization.
--  lane is open TEXT vocabulary (like deep_agent_states.node): decision
--  rows carry the decision template's key, tokenization rows the op's
--  lane. Purge mirrors the request chains: deleting a thread resolves
--  the same chain-id set llm.llm_requests resolves and deletes these
--  rows with it (store chain purge + 09_deep_agent_runs_prune.sql).
--
--  WRITER: llm_agents/_core/aux_steps.py (AuxStepRecorder — the same
--  queue + daemon-writer shape as the request log; fail-soft, off the
--  request path). NEVER loaded into an LLM conversation.
-- ============================================================================

CREATE TABLE IF NOT EXISTS llm.deep_agent_aux_steps (
    step_id          BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id        TEXT,                  -- the deep-agent thread the op ran under (NULL when the caller only knows the chain)
    super_thread_id  TEXT,                  -- the CHAIN id (the run's root thread id) when known — the purge/prune's secondary handle
    state_id         BIGINT      REFERENCES llm.deep_agent_states (state_id) ON DELETE CASCADE,
                     -- the task state the op served, when the caller knows it
                     -- (the context-tags lane does; chain-join covers the rest)
    llm_context_id   TEXT,                  -- the ambient request chain (uuid hex) the op ran under — the UI steps view's JOIN KEY (= llm_requests.chain_id)
    step_type        SMALLINT    NOT NULL,  -- agent_step_type: 2 decision | 3 tokenization (static.label_dim)
    lane             TEXT        NOT NULL,  -- which aux op: decision = the template key (resume_judgment | node_spawn | route_spawn | route_join | failure_probe | kg_relevance | content_sentiment | content_domain | content_industry); tokenization = search_tsv | context_tags | query_segment
    title            TEXT        NOT NULL,  -- one human line (the steps row's headline)
    input_text       TEXT,                  -- compact input face (the facts summary / the raw char counts) for the UI's content view
    output_text      TEXT,                  -- compact output face (the verdict line / the token count)
    input_payload    JSONB,                 -- the typed facts as sent (size-capped recorder-side; the UI's raw view)
    output_payload   JSONB,                 -- the typed result (probabilities/action/ok/note/model, or the token counts; size-capped)
    duration_ms      INT,                   -- wall time of the op (NULL when batch-shared, e.g. the writer thread's batched segment call)
    is_failed        BOOLEAN    NOT NULL DEFAULT false,
                     -- the op itself errored (host down, contract breach) —
                     -- a below-floor/undecisive READ is still a successful op
    error            TEXT,                  -- the failure reason (is_failed rows)
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The steps view's join: every UI read narrows to ONE task state's chain.
CREATE INDEX IF NOT EXISTS ix_deep_agent_aux_steps_chain
    ON llm.deep_agent_aux_steps (llm_context_id)
    WHERE llm_context_id IS NOT NULL;

-- Thread-scoped reads (the purge resolution + any per-thread listing).
CREATE INDEX IF NOT EXISTS ix_deep_agent_aux_steps_thread
    ON llm.deep_agent_aux_steps (thread_id, created_at);

COMMENT ON TABLE llm.deep_agent_aux_steps IS
    'A deep-agent run''s AUXILIARY steps — the System One (jev) decision '
    'reads and the StructBERT tokenization operations — one row per op, '
    'recorded fail-soft off the request path (llm_agents/_core/'
    'aux_steps.py). Their outputs are excluded from every LLM context; '
    'the rows exist for observability: the UI''s Thread Graph "Agent '
    'steps" list joins them onto a task state by llm_context_id (the '
    'same join the inference/request rows use). step_type 2 decision | '
    '3 tokenization (agent_step_type); lane names the op (the decision '
    'template''s key / the tokenization lane).';

COMMENT ON COLUMN llm.deep_agent_aux_steps.llm_context_id IS
    'The ambient request chain (uuid hex) the op ran under — the SAME '
    'key as llm.llm_requests.chain_id and the state''s llm_context_id, '
    'so the steps view joins these rows beside the conversation''s '
    'inference rows. NULL when the op ran outside any bound chain '
    '(e.g. a supervisor read before its context began) — such rows '
    'stay reachable by thread_id / super_thread_id for audit but do '
    'not render under a task.';

COMMENT ON COLUMN llm.deep_agent_aux_steps.step_type IS
    'The step family (static.label_dim domain agent_step_type): '
    '2 decision = one System One read (the typed facts in, the '
    'calibrated verdict out), 3 tokenization = one Chinese '
    'segmentation op (raw chars in, search tokens out). The UI '
    'matches the numeric code; the label mirrors are the vocabulary '
    'of record.';

COMMENT ON COLUMN llm.deep_agent_aux_steps.lane IS
    'Which aux op within the step_type: decision rows carry the '
    'decision template''s key (resume_judgment | node_spawn | '
    'route_spawn | route_join | failure_probe | kg_relevance | '
    'content_sentiment | content_domain | content_industry); '
    'tokenization rows carry the op lane (search_tsv = a request-log '
    'row''s search vector, context_tags = a context row''s tags '
    'vector, query_segment = a search query''s pre-segmentation). '
    'TEXT: open vocabulary like deep_agent_states.node.';

COMMENT ON COLUMN llm.deep_agent_aux_steps.is_failed IS
    'The OP errored (host down, HTTP error, contract breach — the '
    'error column carries why). A below-floor or not-decisive READ '
    'is a successful op: its verdict simply fell through to the '
    'caller''s generative lane (recorded in output_text/note, '
    'is_failed stays false).';
