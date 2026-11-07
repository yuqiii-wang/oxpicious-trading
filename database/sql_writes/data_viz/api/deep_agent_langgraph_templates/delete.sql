-- SQL write action — llm.deep_agent_langgraph_templates DELETE (UI:
-- Thread Graph, saved-template delete). Executed by
-- data_viz/api/services/ai-plan-template.service.ts through the pg
-- primary pool; convention of this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 template_id  INTEGER — the row to delete
--
-- No row had the id → empty RETURNING (the caller maps it to "missing").
DELETE FROM llm.deep_agent_langgraph_templates
 WHERE template_id = $1
RETURNING template_id::int AS template_id
