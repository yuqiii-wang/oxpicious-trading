-- SQL write action — llm.deep_agent_contexts UPDATE (link step-input
-- entries to their state rows). Executed by
-- llm_agents/deep_agents/planning/store.py
-- (PlanStore._backfill_input_state_ids) after a batch of state inserts:
-- a state insert needs its input_context_id FIRST, so the entries mint
-- before their state rows exist — this one scoped UPDATE links them
-- after the inserts (and heals any legacy unlinked rows of the thread
-- the same way).
--
-- Params (asyncpg $n, in order):
--   $1 thread_id  TEXT — the thread whose rows to link
--   $2 kind       SMALLINT — the 'agent_context_kind' code for
--                 'step-input' (the only kind linked here)
UPDATE llm.deep_agent_contexts c SET state_id = s.state_id
FROM llm.deep_agent_states s
WHERE s.thread_id = $1 AND s.input_context_id = c.context_id
AND c.state_id IS NULL AND c.kind = $2
