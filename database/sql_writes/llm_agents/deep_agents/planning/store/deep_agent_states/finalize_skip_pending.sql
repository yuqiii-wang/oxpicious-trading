-- SQL write action — llm.deep_agent_states UPDATE (finalize: settle the
-- run's still-pending rows as skipped). Executed by
-- llm_agents/deep_agents/planning/store.py (PlanStore.finalize_thread,
-- second write, right after finalize_fail_running): never reached.
--
-- Params (asyncpg $n, in order):
--   $1 thread_id  TEXT — the thread finalizing
UPDATE llm.deep_agent_states SET status = 'skipped',
finished_at = now(), error = 'run ended before this step started'
WHERE thread_id = $1 AND status = 'pending'
