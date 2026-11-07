-- SQL write action — llm.deep_agent_states UPDATE (settle not-yet-run
-- steps as skipped). Executed by llm_agents/deep_agents/planning/store.py
-- (PlanStore.skip_pending_states — the plan-update tool's remove op): a
-- replan dropping pending work keeps the audit trail — the row stays,
-- wearing the removal reason. Only pending rows move.
--
-- Params (asyncpg $n, in order):
--   $1 state_ids  BIGINT[] — the pending rows to settle
--   $2 reason     TEXT — the removal reason (capped host-side)
--
-- RETURNING (not a bare UPDATE) makes the moved-row count real for the
-- caller's STATES_SETTLED event.
UPDATE llm.deep_agent_states SET status = 'skipped',
finished_at = now(), error = $2
WHERE state_id = ANY($1::bigint[]) AND status = 'pending'
RETURNING state_id
