-- SQL write action — llm.deep_agent_states UPDATE (REPAIR LANE re-open,
-- the supervisor's retry primitive): flip a FAILED state row back to
-- pending. Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.reopen_state) from the repair lane's normal routing
-- (supervisor/poller.py) after a transient-failure verdict.
--
-- Params (asyncpg $n, in order):
--   $1 state_id  BIGINT — the failed row to reopen
--
-- error/finished_at clear (a later successful settle COALESCEs over
-- error and would otherwise resurrect the failed attempt's message).
-- Guarded failed→pending ONLY (a completed/skipped row never reopens; a
-- concurrent settle makes the guard miss and this returns no rows).
UPDATE llm.deep_agent_states
SET status = 'pending', error = NULL, finished_at = NULL
WHERE state_id = $1 AND status = 'failed'
RETURNING state_id
