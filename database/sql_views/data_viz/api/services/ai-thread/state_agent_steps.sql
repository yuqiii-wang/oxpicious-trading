-- Thread Graph — one task's (plan step / subagent child) AGENT STEPS:
-- the "llm requests" surface replaced by a four-type step list
-- (2026-11-12; aux types 2026-11-13). An INFERENCE step (step_type 0)
-- is one LLM request — the chain-bound llm_requests rows (a TOP-LEVEL
-- step shares the execute node's context with its siblings, so its
-- rows narrow to a ±2s tick window; a spawned-subagent child owns its
-- whole chain — no window). A SPAWN step (step_type 1) is ONE spawned
-- sub node collapsed: the input is the fresh prompt the spawn was given
-- (the child state's goal title), the output is the sub node's FINAL
-- output (its answer, or its error when the spawn failed — the parent
-- collects the error and continues); the sub node's own intermediates
-- (its inference steps and its own spawns) are NEVER inlined here —
-- they stay at the child node, which lists them through this same
-- view. The spawn step is sequential on the parent's timeline (the
-- parent awaits the child): its duration is the wall span
-- started_at→finished_at and its tokens are the CACHE-AWARE rollup
-- over the child's OWN chain (the same least(prev, cur) overlap sum
-- the llm_usage view computes). A DECISION step (step_type 2) is one
-- System One (jev) read and a TOKENIZATION step (step_type 3) is one
-- StructBERT segmentation op — both from llm.deep_agent_aux_steps,
-- joined by the SAME chain id + ±2s window rule as the inference rows
-- (rows recorded outside any bound chain — e.g. a supervisor read
-- before its context began — carry NULL llm_context_id and never
-- render under a task; they stay DB-auditable). step_type decodes via
-- the agent_step_type label domain. A FORK step (step_type 4,
-- 2026-11-13) is the SPAWN step's inherit-context counterpart: the
-- child kind comes from llm.deep_agent_states.child_kind
-- (agent_child_kind — 0/NULL spawn, 1 fork), the fork child's step_id
-- carries the fork- prefix and its collapse semantics equal the
-- spawn's (input = the fork directive's goal title, output = the
-- handback/answer or error) — the UI lists it under its own label so
-- the two child kinds read apart.
-- Serves GET /api/ai/threads/:threadId/states/:stateId/agent-steps.
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
     ),
inf AS (
       SELECT r.request_id::text          AS step_id,
              0                            AS step_type,
              s.llm_context_id             AS llm_context_id,
              NULL::int                    AS child_state_id,
              NULL::text                   AS input_text,
              NULL::text                   AS output_text,
              r.provider,
              r.model,
              r.status_code::int           AS status_code,
              r.is_failed,
              r.failure_reason,
              r.duration_ms::int           AS duration_ms,
              r.input_tokens::int          AS input_tokens,
              r.output_tokens::int         AS output_tokens,
              r.total_tokens::int          AS total_tokens,
              NULL::int                    AS cache_tokens,
              r.request_payload,
              r.response_payload,
              NULL::text                   AS child_status,
              (r.created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at
         FROM s
         JOIN llm.llm_requests r
           ON r.chain_id = s.llm_context_id
          AND (s.parent_state_id IS NOT NULL
               OR ((s.started_at IS NOT NULL OR s.finished_at IS NOT NULL)
                   AND r.created_at >= s.win_start - interval '2 seconds'
                   AND r.created_at <= COALESCE(s.finished_at, now())
                                          + interval '2 seconds'))
     ),
spawn AS (
       SELECT 'spawn-' || c.state_id::text AS step_id,
              1                           AS step_type,
              c.llm_context_id            AS llm_context_id,
              c.state_id::int             AS child_state_id,
              c.title                     AS input_text,
              COALESCE(
                x.payload->>'answer',
                c.error,
                x.payload->>'error')      AS output_text,
              (SELECT min(r.provider) FROM llm.llm_requests r
                WHERE r.chain_id = c.llm_context_id)  AS provider,
              (SELECT min(r.model) FROM llm.llm_requests r
                WHERE r.chain_id = c.llm_context_id)  AS model,
              NULL::int                   AS status_code,
              (c.status = 'failed')       AS is_failed,
              c.error                     AS failure_reason,
              (EXTRACT(epoch FROM (COALESCE(c.finished_at, now())
                                    - COALESCE(c.started_at, c.created_at))
                          ) * 1000)::int AS duration_ms,
              COALESCE(t.input_tokens, 0)::int   AS input_tokens,
              COALESCE(t.output_tokens, 0)::int  AS output_tokens,
              COALESCE(t.total_tokens, 0)::int   AS total_tokens,
              COALESCE(t.cache_tokens, 0)::int   AS cache_tokens,
              NULL::jsonb                 AS request_payload,
              NULL::jsonb                 AS response_payload,
              c.status                    AS child_status,
              (COALESCE(c.finished_at, c.created_at)
                AT TIME ZONE 'Asia/Shanghai')::text AS created_at
         FROM llm.deep_agent_states c
         LEFT JOIN LATERAL (
                SELECT sum(r.input_tokens) AS input_tokens,
                       sum(r.output_tokens) AS output_tokens,
                       sum(r.total_tokens) AS total_tokens,
                       sum(least(prev.input_tokens, r.input_tokens))
                         FILTER (WHERE prev.input_tokens IS NOT NULL
                                  AND r.input_tokens IS NOT NULL)
                           AS cache_tokens
                  FROM llm.llm_requests r
                  LEFT JOIN llm.llm_requests prev
                         ON prev.request_id = r.prev_request_id
                 WHERE r.chain_id = c.llm_context_id) t
               ON c.llm_context_id IS NOT NULL
         LEFT JOIN llm.deep_agent_contexts x
                ON x.context_id = c.result_context_id
        WHERE c.thread_id = {{thread_id}}
          AND c.parent_state_id = {{state_id}}
          AND (c.child_kind IS NULL OR c.child_kind = 0)
     ),
-- The FORK step (step_type 4): the SAME collapse shape as the spawn
-- CTE above, for the INHERIT-context children (child_kind = 1,
-- agent_child_kind fork — the child continued a deep copy of its
-- parent's transcript; the UI draws its canvas edge thicker than a
-- spawn's). Deliberately a separate filtered SELECT, no value-mapping
-- in this view.
fork AS (
       SELECT 'fork-' || c.state_id::text AS step_id,
              4                           AS step_type,
              c.llm_context_id            AS llm_context_id,
              c.state_id::int             AS child_state_id,
              c.title                     AS input_text,
              COALESCE(
                x.payload->>'answer',
                c.error,
                x.payload->>'error')      AS output_text,
              (SELECT min(r.provider) FROM llm.llm_requests r
                WHERE r.chain_id = c.llm_context_id)  AS provider,
              (SELECT min(r.model) FROM llm.llm_requests r
                WHERE r.chain_id = c.llm_context_id)  AS model,
              NULL::int                   AS status_code,
              (c.status = 'failed')       AS is_failed,
              c.error                     AS failure_reason,
              (EXTRACT(epoch FROM (COALESCE(c.finished_at, now())
                                    - COALESCE(c.started_at, c.created_at))
                          ) * 1000)::int AS duration_ms,
              COALESCE(t.input_tokens, 0)::int   AS input_tokens,
              COALESCE(t.output_tokens, 0)::int  AS output_tokens,
              COALESCE(t.total_tokens, 0)::int   AS total_tokens,
              COALESCE(t.cache_tokens, 0)::int   AS cache_tokens,
              NULL::jsonb                 AS request_payload,
              NULL::jsonb                 AS response_payload,
              c.status                    AS child_status,
              (COALESCE(c.finished_at, c.created_at)
                AT TIME ZONE 'Asia/Shanghai')::text AS created_at
         FROM llm.deep_agent_states c
         LEFT JOIN LATERAL (
                SELECT sum(r.input_tokens) AS input_tokens,
                       sum(r.output_tokens) AS output_tokens,
                       sum(r.total_tokens) AS total_tokens,
                       sum(least(prev.input_tokens, r.input_tokens))
                         FILTER (WHERE prev.input_tokens IS NOT NULL
                                  AND r.input_tokens IS NOT NULL)
                           AS cache_tokens
                  FROM llm.llm_requests r
                  LEFT JOIN llm.llm_requests prev
                         ON prev.request_id = r.prev_request_id
                 WHERE r.chain_id = c.llm_context_id) t
               ON c.llm_context_id IS NOT NULL
         LEFT JOIN llm.deep_agent_contexts x
                ON x.context_id = c.result_context_id
        WHERE c.thread_id = {{thread_id}}
          AND c.parent_state_id = {{state_id}}
          AND c.child_kind = 1
     ),
-- One System One (jev) decision read (llm.deep_agent_aux_steps,
-- step_type 2): input = the compact facts face, output = the verdict
-- line (action p=.. ok=..), the typed payloads ride the raw view. The
-- read's payload is NEVER in any LLM conversation — this list is its
-- display surface. Same chain join + window rule as the inference
-- rows (the reads run inside the conversation's bound chain).
dec AS (
       SELECT 'dec-' || x.step_id::text AS step_id,
              2                          AS step_type,
              x.llm_context_id           AS llm_context_id,
              NULL::int                  AS child_state_id,
              x.input_text               AS input_text,
              x.output_text              AS output_text,
              'jev'                      AS provider,
              (x.output_payload ->> 'model') AS model,
              NULL::int                  AS status_code,
              x.is_failed,
              x.error                    AS failure_reason,
              x.duration_ms::int         AS duration_ms,
              0::int                     AS input_tokens,
              0::int                     AS output_tokens,
              0::int                     AS total_tokens,
              NULL::int                  AS cache_tokens,
              x.input_payload            AS request_payload,
              x.output_payload           AS response_payload,
              NULL::text                 AS child_status,
              (x.created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at
         FROM s
         JOIN llm.deep_agent_aux_steps x
           ON x.llm_context_id = s.llm_context_id
          AND x.step_type = 2
          AND (s.parent_state_id IS NOT NULL
               OR ((s.started_at IS NOT NULL OR s.finished_at IS NOT NULL)
                   AND x.created_at >= s.win_start - interval '2 seconds'
                   AND x.created_at <= COALESCE(s.finished_at, now())
                                          + interval '2 seconds'))
     ),
-- One StructBERT tokenization op (step_type 3): search_tsv (a
-- request-log row's vector, per inference row), context_tags (a
-- context row's tags vector) or query_segment (the search tool's
-- query pre-segmentation). duration is per-op where the op is
-- singular; the writer thread's batch-shared segmentation records
-- none.
tok AS (
       SELECT 'tok-' || x.step_id::text AS step_id,
              3                          AS step_type,
              x.llm_context_id           AS llm_context_id,
              NULL::int                  AS child_state_id,
              x.input_text               AS input_text,
              x.output_text              AS output_text,
              NULL::text                 AS provider,
              NULL::text                 AS model,
              NULL::int                  AS status_code,
              x.is_failed,
              x.error                    AS failure_reason,
              x.duration_ms::int         AS duration_ms,
              0::int                     AS input_tokens,
              0::int                     AS output_tokens,
              0::int                     AS total_tokens,
              NULL::int                  AS cache_tokens,
              x.input_payload            AS request_payload,
              x.output_payload           AS response_payload,
              NULL::text                 AS child_status,
              (x.created_at AT TIME ZONE 'Asia/Shanghai')::text AS created_at
         FROM s
         JOIN llm.deep_agent_aux_steps x
           ON x.llm_context_id = s.llm_context_id
          AND x.step_type = 3
          AND (s.parent_state_id IS NOT NULL
               OR ((s.started_at IS NOT NULL OR s.finished_at IS NOT NULL)
                   AND x.created_at >= s.win_start - interval '2 seconds'
                   AND x.created_at <= COALESCE(s.finished_at, now())
                                          + interval '2 seconds'))
     )
SELECT step_id, step_type, llm_context_id, child_state_id,
       input_text, output_text, provider, model, status_code,
       is_failed, failure_reason, duration_ms,
       input_tokens, output_tokens, total_tokens, cache_tokens,
       request_payload, response_payload, child_status, created_at
  FROM inf
UNION ALL
SELECT step_id, step_type, llm_context_id, child_state_id,
       input_text, output_text, provider, model, status_code,
       is_failed, failure_reason, duration_ms,
       input_tokens, output_tokens, total_tokens, cache_tokens,
       request_payload, response_payload, child_status, created_at
  FROM spawn
UNION ALL
SELECT step_id, step_type, llm_context_id, child_state_id,
       input_text, output_text, provider, model, status_code,
       is_failed, failure_reason, duration_ms,
       input_tokens, output_tokens, total_tokens, cache_tokens,
       request_payload, response_payload, child_status, created_at
  FROM fork
UNION ALL
SELECT step_id, step_type, llm_context_id, child_state_id,
       input_text, output_text, provider, model, status_code,
       is_failed, failure_reason, duration_ms,
       input_tokens, output_tokens, total_tokens, cache_tokens,
       request_payload, response_payload, child_status, created_at
  FROM dec
UNION ALL
SELECT step_id, step_type, llm_context_id, child_state_id,
       input_text, output_text, provider, model, status_code,
       is_failed, failure_reason, duration_ms,
       input_tokens, output_tokens, total_tokens, cache_tokens,
       request_payload, response_payload, child_status, created_at
  FROM tok
 ORDER BY created_at, step_id
