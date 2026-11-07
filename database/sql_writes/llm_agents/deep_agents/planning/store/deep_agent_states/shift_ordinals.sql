-- SQL write action — llm.deep_agent_states UPDATE (open an ordinal gap
-- for runtime-added top-level steps). Executed by
-- llm_agents/deep_agents/planning/store.py (PlanStore.add_plan_states,
-- parent-less adds with an explicit after_ordinal): every later row
-- shifts up so the new steps take after_ordinal + 1..n.
--
-- Params (asyncpg $n, in order):
--   $1 thread_id      TEXT — the thread whose top-level rows shift
--   $2 after_ordinal  INTEGER — the position the new steps follow
--   $3 n_new          INTEGER — how many steps are being inserted
UPDATE llm.deep_agent_states SET ordinal = ordinal + $3
WHERE thread_id = $1 AND parent_state_id IS NULL
AND ordinal > $2
