-- SQL write action — llm.deep_agent_states UPDATE (repoint a revised
-- step at its NEW input context). Executed by
-- llm_agents/deep_agents/planning/store.py (PlanStore.revise_pending_state):
-- a notes/delegate change mints a NEW step-input context (contexts are
-- append-only) and repoints the row.
--
-- Params (asyncpg $n, in order):
--   $1 state_id           BIGINT — the revised row
--   $2 input_context_id   BIGINT — the freshly minted step-input
--                         context's id
--
-- Returns the full snapshot row for the caller's STATE_UPDATED event.
UPDATE llm.deep_agent_states SET input_context_id = $2
WHERE state_id = $1
RETURNING state_id, thread_id, parent_state_id, node, title, status, ordinal, input_context_id, result_context_id, error, started_at, finished_at, llm_context_id
