-- SQL write action — llm.deep_agent_states UPDATE (REPAIR LANE
-- ignore-settle, the supervisor's skip verdict): flip a FAILED state row
-- to skipped wearing the supervisor's note — the run proceeds PAST the
-- step (its work is judged dispensable or the fault
-- structural-but-non-fatal). Executed by
-- llm_agents/deep_agents/planning/store.py (PlanStore.ignore_state).
--
-- Params (asyncpg $n, in order):
--   $1 state_id  BIGINT — the failed row to settle
--   $2 note      TEXT — the supervisor's ignore note (capped host-side)
--
-- Guarded failed→skipped ONLY (a pending/completed row never flips).
UPDATE llm.deep_agent_states
SET status = 'skipped', error = $2, finished_at = now()
WHERE state_id = $1 AND status = 'failed'
RETURNING state_id
