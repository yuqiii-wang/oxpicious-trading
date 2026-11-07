-- SQL write action — llm.deep_agent_contexts INSERT (the table's ONE
-- write place). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore._insert_context) for every context kind (plan, step-input,
-- step-result, subagent-result, final-result, graph-output,
-- supervisor-review); convention of this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (asyncpg $n, in order):
--   $1 thread_id      TEXT — the owning thread (graph segment) of the run
--   $2 kind           SMALLINT — the static.label_dim 'agent_context_kind'
--                     code (resolved host-side via _common.labels
--                     AGENT_CONTEXT_KIND_CODE; never a CASE map)
--   $3 payload        JSONB — the entry's document (contexts are
--                     referenced by uuid, never persisted transcripts)
--   $4 node           TEXT | NULL — the entry's node key
--   $5 state_id       BIGINT | NULL — the task row the entry belongs to
--   $6 llm_context_id TEXT | NULL — the ONE chained LLM context (uuid
--                     hex) of the node conversation that produced it —
--                     the key linking the entry to its llm.llm_requests
--                     rows (llm_requests.chain_id)
--   $7 is_critical    BOOLEAN — lifted from the payload's "critical":
--                     true flag (the model's run-conclusion mark)
--   $8 tags_text      TEXT — the payload's important text
--                     (string-leaf joined, capped), StructBERT-tokenized
--                     host-side; tokenized here into the GIN-indexed
--                     tags vector the thread-internal search matches
--
-- Returns the new context_id.
INSERT INTO llm.deep_agent_contexts
    (thread_id, kind, payload, node, state_id, llm_context_id,
     is_critical, tags)
VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7,
        strip(to_tsvector('simple', $8))) RETURNING context_id
