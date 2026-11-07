-- Thread Graph — one thread's deep-agent plan-step and spawned-subagent
-- state rows (llm.deep_agent_states), plan order. The BIGINT id columns
-- cast to int because node-postgres hands int8 back as STRING while the
-- UI contract is number.
-- Serves GET /api/ai/threads/:threadId (the states block).
-- args:
--   thread_id: value text — the deep-agent thread (uuid hex)
SELECT state_id::int AS state_id, thread_id,
            parent_state_id::int AS parent_state_id, node, title, status,
            ordinal, input_context_id::int AS input_context_id,
            result_context_id::int AS result_context_id, error,
            llm_context_id,
            (created_at AT TIME ZONE 'Asia/Shanghai')::text  AS created_at,
            (started_at AT TIME ZONE 'Asia/Shanghai')::text  AS started_at,
            (finished_at AT TIME ZONE 'Asia/Shanghai')::text AS finished_at
       FROM llm.deep_agent_states
      WHERE thread_id = {{thread_id}}
      ORDER BY ordinal, state_id
