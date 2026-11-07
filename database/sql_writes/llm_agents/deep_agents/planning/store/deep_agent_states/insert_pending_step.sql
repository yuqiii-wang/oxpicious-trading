-- SQL write action — llm.deep_agent_states INSERT (one PENDING plan
-- step). Executed by llm_agents/deep_agents/planning/store.py twice —
-- PlanStore.open_custom_thread (the batched proposal's first validated
-- batch) and PlanStore.create_thread_states (later batches and the
-- result graph's review-wave seats): one pending row per step, stamped
-- with the run's CHAIN id, its `notes` already persisted as the step's
-- input context (input_context_id).
--
-- Params (asyncpg $n, in order):
--   $1 thread_id        TEXT — the CUSTOM thread the steps run in
--   $2 super_thread_id  TEXT | NULL — the chain id
--   $3 node             TEXT — the step's node key
--   $4 title            TEXT — the step's title (capped host-side)
--   $5 ordinal          INTEGER — the 1-based plan order (top level)
--   $6 input_context_id BIGINT | NULL — the step-input context minted
--                       for the step's notes (None when it had none)
--
-- Returns the full snapshot row (the _STATE_COLUMNS list) for the
-- caller's StateRow + its STATE_CREATED event.
INSERT INTO llm.deep_agent_states
    (thread_id, super_thread_id, node, title, ordinal,
     input_context_id)
VALUES ($1, $2, $3, $4, $5, $6)
RETURNING state_id, thread_id, parent_state_id, node, title, status, ordinal, input_context_id, result_context_id, error, started_at, finished_at, llm_context_id
