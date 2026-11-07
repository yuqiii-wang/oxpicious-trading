-- SQL write action — langgraph.checkpoint_blobs DELETE (UI: Summary
-- History, entry delete). Executed by
-- data_viz/api/services/ai-summary.service.ts (deleteSummaryRun) through
-- the pg primary client inside the per-member transaction; convention of
-- this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 thread_id       TEXT — ONE chain member's thread id
DELETE FROM langgraph.checkpoint_blobs WHERE thread_id = $1
