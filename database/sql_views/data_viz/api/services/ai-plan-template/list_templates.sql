-- Saved LangGraph templates — the generate form's picker rows
-- (llm.deep_agent_langgraph_templates), name order. placeholders are
-- computed from plan_json in the service, not here. Only TYPE-PLAN rows
-- (static.label_dim domain 'langgraph_type' code 0 — the saved plans
-- the Thread Graph Save button writes) are listed: the picker starts a
-- run's PLAN from them, and the daemon's plan lane loads type-0 rows
-- only (templates.fetch_plan_template filters type = 0). The other
-- codes exist but are not plan templates — 1 custom is reserved
-- vocabulary, 2 result is the seeded review-wave definition whose row
-- (default-result-wave) DRIVES the result graph; listing it here would
-- read as pickable while the daemon silently falls back to the LLM
-- planning session on it (2026-11-07 fix).
-- Serves GET /api/ai/plan-templates.
-- args: none
SELECT template_id::int AS template_id, name, type::int AS type,
            thread_id, args,
            plan_json, n_steps::int AS n_steps,
            (created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at,
            (updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at
       FROM llm.deep_agent_langgraph_templates
      WHERE type = 0
      ORDER BY name
