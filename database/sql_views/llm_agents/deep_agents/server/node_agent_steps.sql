-- Thread Graph — one RUNNER-LANE node's (the RESULT/CUSTOM graph's
-- output node — the seats with NO deep_agent_states row, 2026-11-15)
-- AGENT STEPS: the same four-type step list as
-- data_viz/api/services/ai-thread/state_agent_steps keyed by the node
-- item's OWN chain instead of a state id. The runner-lane node's store
-- item (llm.deep_agent_store_items key node:<name>) carries the node
-- conversation's llm_context_id — its whole chain IS the node's
-- conversation (one node one context, no sibling sharing, no window).
-- Spawn/fork CTEs are absent by construction: a runner-lane node
-- parents no child state row (the output seat's spawns are disabled).
-- Serves GET /api/ai/threads/:threadId/nodes/:node/agent-steps.
-- args:
--   thread_id: value text — the OWNING segment thread (the service
--              resolves the canvas node id's s<k>: prefix to the
--              segment's thread — every graph segment writes its own
--              node:output item)
--   node:      value text — the runner-lane node name (e.g. output)
WITH s AS (
       SELECT value->>'llm_context_id' AS llm_context_id
         FROM llm.deep_agent_store_items
        WHERE namespace = ARRAY['deep_agent', {{thread_id}}]
          AND key = 'node:' || {{node}}
          AND value->>'llm_context_id' IS NOT NULL
        LIMIT 1
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
     ),
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
     ),
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
  FROM dec
UNION ALL
SELECT step_id, step_type, llm_context_id, child_state_id,
       input_text, output_text, provider, model, status_code,
       is_failed, failure_reason, duration_ms,
       input_tokens, output_tokens, total_tokens, cache_tokens,
       request_payload, response_payload, child_status, created_at
  FROM tok
 ORDER BY created_at, step_id
