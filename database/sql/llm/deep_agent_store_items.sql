
CREATE TABLE IF NOT EXISTS llm.deep_agent_store_items (
    item_id     BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    namespace   TEXT[]      NOT NULL,  -- ("deep_agent", <thread_id>[, "contexts"])
    key         TEXT        NOT NULL,  -- node:<name> | <llm_context_id>
    value       JSONB       NOT NULL,  -- the state item (closed schema — see below)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The store's access paths: exact-namespace item reads (the ThreadGraph
-- detail's one round trip) ride the UNIQUE btree, and prefix searches
-- (namespace[1:n] equality) scan — the table holds a handful of items per
-- run and runs are pruned daily, far below any index-justifying size.
CREATE UNIQUE INDEX IF NOT EXISTS ux_deep_agent_store_items_ns_key
    ON llm.deep_agent_store_items (namespace, key);

COMMENT ON TABLE llm.deep_agent_store_items IS
    'The deep-agent LangGraph store: BaseStore (langgraph.store.base) '
    'namespace/key items implemented by DeepAgentStore '
    '(llm_agents/deep_agents/langgraph/state_store.py) and hooked into the runner '
    'via compile(store=...). PERSISTED HERE: node-execution STATES (the '
    'runner-lane plan/execute/finalize lifecycle — status, timestamps, '
    'counts) and CONTEXT REFERENCES (one registry item per LLM context: '
    'node, langgraph checkpoint id, call counts). NEVER PERSISTED: LLM '
    'context data — messages, system prompts, tool transcripts. Namespace '
    'layout and the policy are spelled out in '
    'database/sql/text/llm/10_deep_agent_store.sql''s header.';

COMMENT ON COLUMN llm.deep_agent_store_items.namespace IS
    'Hierarchical namespace (LangGraph store semantics): ("deep_agent", '
    '<thread_id>) for node states, ("deep_agent", <thread_id>, '
    '"contexts") for the context registry. thread_id is the run''s uuid '
    'hex — the same key as llm.deep_agent_states.thread_id and the '
    'langgraph checkpoints.';

COMMENT ON COLUMN llm.deep_agent_store_items.key IS
    'Unique within the namespace: node:<plan|execute|finalize> for node '
    'states, the llm_context_id (uuid hex) for context-registry items.';

COMMENT ON COLUMN llm.deep_agent_store_items.value IS
    'The state item — a CLOSED schema (the writer whitelists fields, so '
    'context data cannot ride in): node items {kind, node, status, '
    'llm_context_id, checkpoint_id, model, error, n_llm_calls, '
    'n_tool_calls, n_states, answer_chars}, context items {kind, node, '
    'state_id, parent_context_id, checkpoint_id, model, n_llm_calls, '
    'n_tool_calls}. Timestamps live on the row columns '
    'created_at / updated_at, not in the value.';
