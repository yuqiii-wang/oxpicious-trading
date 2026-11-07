-- SQL write action — llm.deep_agent_contexts UPDATE (heal one row's NULL
-- tags vector). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.backfill_context_tags_async) via asyncpg executemany over
-- the work-queue chunks (rows whose tags IS NULL — the NULL filter IS
-- the queue, so the pass is resumable); the mirror of request_log's
-- backfill_search_tsv.
--
-- Params (asyncpg $n, per row, in order):
--   $1 context_id  BIGINT — the row to heal
--   $2 tags_text   TEXT — the stored payload's tokenized text (NULL
--                  stays NULL — a tokenizer failure leaves the row for
--                  the next pass)
UPDATE llm.deep_agent_contexts
SET tags = strip(to_tsvector('simple', $2))
WHERE context_id = $1
