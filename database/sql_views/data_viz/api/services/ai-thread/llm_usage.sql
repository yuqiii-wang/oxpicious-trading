-- Thread Graph — per-context LLM traffic: the thread's llm.llm_requests
-- rolled up by chain_id (= the context id): request count, LLM busy time,
-- first→last wall span, token sums. Chain set = the node items' live
-- context ids unioned with the registry items.
-- Serves GET /api/ai/threads/:threadId (the llm_usage block).
-- args:
--   chain_ids: value text[] — the thread's LLM context (chain) ids
SELECT chain_id AS context_id,
                  count(*)::int AS n_requests,
                  sum(duration_ms)::int AS llm_ms,
                  EXTRACT(epoch FROM (max(created_at) - min(created_at)))::int
                    AS span_s,
                  sum(input_tokens)::int  AS input_tokens,
                  sum(output_tokens)::int AS output_tokens,
                  sum(total_tokens)::int  AS total_tokens,
                  (min(created_at) AT TIME ZONE 'Asia/Shanghai')::text AS first_at,
                  (max(created_at) AT TIME ZONE 'Asia/Shanghai')::text AS last_at
             FROM llm.llm_requests
            WHERE chain_id = ANY({{chain_ids}}::text[])
            GROUP BY chain_id
