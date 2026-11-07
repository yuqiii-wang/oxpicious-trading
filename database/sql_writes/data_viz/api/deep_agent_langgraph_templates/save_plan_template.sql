-- SQL write action — llm.deep_agent_langgraph_templates INSERT/UPSERT
-- (UI: Thread Graph, Save plan template). Executed by
-- data_viz/api/services/ai-plan-template.service.ts through the pg
-- primary pool; convention of this tree:
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 name         TEXT — the template's unique display name (the
--                   conflict key: a re-save under the same name replaces
--                   the stored template)
--   $2 type         SMALLINT — static.label_dim 'langgraph_type' plan code
--   $3 thread_id    TEXT — the run's thread the template was saved from
--   $4 args         JSONB — the placeholder args dictionary (question /
--                   sec_type / code / name) for re-materialization
--   $5 plan_json    JSONB — the templatized plan payload (steps with
--                   {{arg}} placeholders)
--   $6 n_steps      INTEGER — the step count
--
-- Returns the stored row in the list payload shape.
INSERT INTO llm.deep_agent_langgraph_templates
   (name, type, thread_id, args, plan_json, n_steps)
 VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
 ON CONFLICT (name) DO UPDATE SET
   thread_id = EXCLUDED.thread_id,
   args = EXCLUDED.args,
   plan_json = EXCLUDED.plan_json,
   n_steps = EXCLUDED.n_steps,
   updated_at = now()
 RETURNING template_id::int AS template_id, name,
   type::int AS type, thread_id, args,
   plan_json, n_steps::int AS n_steps,
   (created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at,
   (updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at
