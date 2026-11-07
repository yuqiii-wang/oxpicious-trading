-- SQL write action — llm.deep_agent_identities DELETE (UI: Summary
-- History, entry delete). Executed by
-- data_viz/api/services/ai-summary.service.ts (deleteSummaryRun) through
-- the pg primary client inside the per-member transaction; convention of
-- this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 thread_id       TEXT — ONE chain member's thread id
DELETE FROM llm.deep_agent_identities WHERE thread_id = $1
