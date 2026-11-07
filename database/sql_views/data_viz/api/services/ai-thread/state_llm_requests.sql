-- Thread Graph task-row expansion — one task's (plan step / subagent
-- child) recorded LLM requests/responses. The state's chain-bound rows
-- join by llm_context_id; a TOP-LEVEL step shares the execute node's
-- context with its siblings, so its rows narrow to a ±2s tick window
-- (started_at / earlier-sibling finished_at / created_at → finished_at,
-- else now()). A spawned-subagent child owns its whole chain — no window.
-- Serves GET /api/ai/threads/:threadId/states/:stateId/llm-requests.
-- args:
--   thread_id: value text — the deep-agent thread (uuid hex)
--   state_id: value bigint — the deep_agent_states row (task)
WITH s AS (
       SELECT state_id, parent_state_id, llm_context_id, created_at,
              started_at, finished_at,
              COALESCE(
                started_at,
                (SELECT max(p.finished_at) FROM llm.deep_agent_states p
                  WHERE p.thread_id = s.thread_id
                    AND p.parent_state_id IS NULL
                    AND p.state_id < s.state_id),
                s.created_at) AS win_start
         FROM llm.deep_agent_states s
        WHERE s.thread_id = {{thread_id}} AND s.state_id = {{state_id}}
     )
     SELECT r.request_id::text          AS request_id,
            s.llm_context_id            AS llm_context_id,
            r.model,
            r.status_code::int          AS status_code,
            r.is_failed,
            r.failure_reason,
            r.duration_ms::int          AS duration_ms,
            r.input_tokens::int         AS input_tokens,
            r.output_tokens::int        AS output_tokens,
            r.total_tokens::int         AS total_tokens,
            r.prev_request_id::text     AS prev_request_id,
            r.request_payload,
            r.response_payload,
            (r.created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at
       FROM s
       JOIN llm.llm_requests r
         ON r.chain_id = s.llm_context_id
        AND (s.parent_state_id IS NOT NULL
             OR ((s.started_at IS NOT NULL OR s.finished_at IS NOT NULL)
                 AND r.created_at >= s.win_start - interval '2 seconds'
                 AND r.created_at <= COALESCE(s.finished_at, now())
                                        + interval '2 seconds'))
      ORDER BY r.request_id
