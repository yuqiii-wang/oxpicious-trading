-- SQL write action — llm.deep_agent_authorizations chain purge (the
-- TS mirror of the llm_agents action of the same name). Executed by
-- data_viz/api/services/ai/ai-summary.service.ts (deleteSummaryRun)
-- inside the chain-delete transaction: rows are keyed by the run's
-- CHAIN id, so the delete matches exactly when the purged member IS
-- the chain root (harmless no-op for the C/R segment members).
--
-- Params (positional $n, in order):
--   $1 thread_id text — the chain member being purged (= the chain id
--                        on the root member)
DELETE FROM llm.deep_agent_authorizations
WHERE super_thread_id = $1
