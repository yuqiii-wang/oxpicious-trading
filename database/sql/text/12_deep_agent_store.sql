-- ============================================================================
--  Deep-agent LangGraph STORE — BaseStore semantics over this repo's storage
--  Tables: text.deep_agent_store_items (the store: namespace[] + key + JSONB)
--  Alter:  text.deep_agent_states + llm_context_id (states reference their
--          LLM context BY ID — the context data itself is never persisted)
--
--  The runner (llm_agents/deep_agents/graph/runner.py) already compiles its
--  StateGraph with the AsyncPostgresSaver CHECKPOINTER (per-superstep state
--  snapshots in the framework-managed langgraph schema, 09_deep_agent_
--  states.sql). This file adds the other LangGraph persistence primitive,
--  the STORE (langgraph.store.base.BaseStore — cross-thread namespace/key
--  memory), implemented by llm_agents/deep_agents/graph/state_store.py as
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
--      text.deep_agent_states row gains llm_context_id so each persisted
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
--  11_deep_agent_runs_prune.sql deletes them alongside the checkpoint
--  tables, AFTER resolving the thread's llm_requests chains from the
--  items (chain_id = the node's llm_context_id — node items' value and
--  the context registry's keys) and deleting those request rows first.
--
--  Applied after 09_deep_agent_states.sql (needs text.deep_agent_states).
-- ============================================================================

ALTER TABLE text.deep_agent_states
    ADD COLUMN IF NOT EXISTS llm_context_id TEXT;

COMMENT ON COLUMN text.deep_agent_states.llm_context_id IS
    'The id of the LLM context this state ran under (uuid hex) — a '
    'REFERENCE ONLY: the context registry item (namespace ("deep_agent", '
    'thread_id, "contexts"), same key) holds the node, the langgraph '
    'checkpoint id and call counts, never the messages. Top-level plan '
    'steps share the execute node''s context id (the agent loop walks '
    'them inside its one conversation — one node one LLM context). '
    'spawned-subagent children each carry their own. NULL when the run '
    'predates the store or the node ran no LLM work.';

CREATE TABLE IF NOT EXISTS text.deep_agent_store_items (
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
    ON text.deep_agent_store_items (namespace, key);

COMMENT ON TABLE text.deep_agent_store_items IS
    'The deep-agent LangGraph store: BaseStore (langgraph.store.base) '
    'namespace/key items implemented by DeepAgentStore '
    '(llm_agents/deep_agents/graph/state_store.py) and hooked into the runner '
    'via compile(store=...). PERSISTED HERE: node-execution STATES (the '
    'runner-lane plan/execute/finalize lifecycle — status, timestamps, '
    'counts) and CONTEXT REFERENCES (one registry item per LLM context: '
    'node, langgraph checkpoint id, call counts). NEVER PERSISTED: LLM '
    'context data — messages, system prompts, tool transcripts. Namespace '
    'layout and the policy are spelled out in '
    'database/sql/text/12_deep_agent_store.sql''s header.';

COMMENT ON COLUMN text.deep_agent_store_items.namespace IS
    'Hierarchical namespace (LangGraph store semantics): ("deep_agent", '
    '<thread_id>) for node states, ("deep_agent", <thread_id>, '
    '"contexts") for the context registry. thread_id is the run''s uuid '
    'hex — the same key as text.deep_agent_states.thread_id and the '
    'langgraph checkpoints.';

COMMENT ON COLUMN text.deep_agent_store_items.key IS
    'Unique within the namespace: node:<plan|execute|finalize> for node '
    'states, the llm_context_id (uuid hex) for context-registry items.';

COMMENT ON COLUMN text.deep_agent_store_items.value IS
    'The state item — a CLOSED schema (the writer whitelists fields, so '
    'context data cannot ride in): node items {kind, node, status, '
    'llm_context_id, checkpoint_id, model, error, n_llm_calls, '
    'n_tool_calls, n_states, answer_chars}, context items {kind, node, '
    'state_id, parent_context_id, checkpoint_id, model, n_llm_calls, '
    'n_tool_calls}. Timestamps live on the row columns '
    'created_at / updated_at, not in the value.';
