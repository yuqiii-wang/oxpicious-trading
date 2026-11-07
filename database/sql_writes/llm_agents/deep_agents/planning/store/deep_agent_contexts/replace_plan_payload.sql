-- SQL write action — llm.deep_agent_contexts UPDATE (rewrite the ONE
-- 'plan' context row's payload in place). Executed by
-- llm_agents/deep_agents/planning/store.py
-- (PlanStore.replace_plan_payload) — the batched proposal session's
-- final write: the complete plan payload lands over the partial one the
-- first batch wrote; the row keeps its original identity/keys. The tags
-- vector refreshes with the payload (the plan grew — it must cover the
-- complete steps, not the first batch's partial).
--
-- Params (asyncpg $n, in order):
--   $1 thread_id   TEXT — the PLAN thread (the chain's root)
--   $2 payload     JSONB — the complete plan document
--   $3 kind        SMALLINT — the 'agent_context_kind' code for 'plan'
--   $4 tags_text   TEXT — the new payload's tokenized text
--
-- The target row is the thread's OLDEST kind-'plan' context (the first
-- batch's row). No match = the create_thread degraded earlier; the
-- caller inserts a fresh row instead (the UI's plan read never 404s).
UPDATE llm.deep_agent_contexts c
SET payload = $2::jsonb,
    tags = strip(to_tsvector('simple', $4))
WHERE c.context_id = (SELECT c2.context_id
FROM llm.deep_agent_contexts c2
WHERE c2.thread_id = $1 AND c2.kind = $3
ORDER BY c2.context_id LIMIT 1)
RETURNING c.context_id
