-- Thread Graph — the LangGraph store's runner-lane NODE states
-- (llm.deep_agent_store_items under ("deep_agent", <tid>), key LIKE
-- 'node:%'): plan/execute/finalize lifecycle + counts, elapsed wall time
-- computed server-side against now() for running nodes so the live poll
-- ticks without client-side timezone math.
-- Serves GET /api/ai/threads/:threadId (the node_states block).
-- args:
--   thread_id: value text — the deep-agent thread (uuid hex)
SELECT (value->>'node')   AS node,
            (value->>'status') AS status,
            value->>'llm_context_id' AS llm_context_id,
            value->>'checkpoint_id'  AS checkpoint_id,
            value->>'model'          AS model,
            value->>'error'          AS error,
            (value->>'n_llm_calls')::int   AS n_llm_calls,
            (value->>'n_tool_calls')::int  AS n_tool_calls,
            (value->>'n_states')::int      AS n_states,
            (value->>'answer_chars')::int  AS answer_chars,
            NULLIF(value->>'state_id', '')::int AS state_id,
            (created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at,
            (updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
            EXTRACT(epoch FROM (
              -- running nodes tick against now(); the boolean cast swaps
              -- the endpoint (running: (u−c)+(now−u) = now−c; else u−c).
              updated_at - created_at
              + ((value->>'status') = 'running')::int * (now() - updated_at)
            ))::int AS elapsed_s
       FROM llm.deep_agent_store_items
      WHERE namespace = ARRAY['deep_agent', {{thread_id}}]::text[]
        AND key LIKE 'node:%'
      ORDER BY key
