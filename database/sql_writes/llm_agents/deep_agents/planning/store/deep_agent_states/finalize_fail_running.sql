-- SQL write action — llm.deep_agent_states UPDATE (finalize: settle the
-- run's still-running rows as failed). Executed by
-- llm_agents/deep_agents/planning/store.py (PlanStore.finalize_thread,
-- first write): the run ended inside them (died mid-step / cancelled /
-- stopped) — the audit trail closes failed wearing the reason.
--
-- Params (asyncpg $n, in order):
--   $1 thread_id  TEXT — the thread finalizing
--   $2 failure    TEXT — why the run ended (capped host-side)
UPDATE llm.deep_agent_states SET status = 'failed',
finished_at = now(), error = $2
WHERE thread_id = $1 AND status = 'in_progress'
