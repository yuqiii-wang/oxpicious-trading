-- Saved plan templates — save side: the source thread's persisted plan
-- payload (the kind 'plan' context the runner stamped at create_thread,
-- first by context_id).
-- Serves POST /api/ai/plan-templates.
-- args:
--   thread_id: value text — the source deep-agent thread (uuid hex)
--   plan_kind_code: fragment — String(LABEL_CODE.agent_context_kind.plan)
SELECT payload FROM llm.deep_agent_contexts
      WHERE thread_id = {{thread_id}} AND kind = {{plan_kind_code}}
      ORDER BY context_id LIMIT 1
