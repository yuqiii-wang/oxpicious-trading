-- Thread Graph — per-context LLM traffic: the thread's llm.llm_requests
-- rolled up by chain_id (= the context id): request count, LLM busy time,
-- first→last wall span, token sums — CACHE-AWARE (2026-11-10): a chained
-- conversation RESENDS its accumulated history on every tool turn, so the
-- naive input-token sum double-counts the overlapping prefix. Chaining
-- each row to its writer-backfilled prev_request_id (the same chain
-- order), a request's re-sent history = least(prev.input_tokens,
-- input_tokens) — the whole predecessor input while the conversation is
-- append-only, capped at the current input after a compaction shrink.
-- cache_tokens sums that overlap: the prefix the provider's context cache
-- serves (a SUBSET of input_tokens, not an extra cost); fresh_input_tokens
-- = input − cache = the genuinely new input — the conversation's final
-- history size (the chain's LAST request's input) while append-only.
-- The overlap counts only where BOTH rows reported a usage block (a
-- failed / usage-less row measures nothing); a chain whose prev link is
-- not yet backfilled undercounts cache fail-soft until the writer heals
-- the link (request_log._BACKFILL_PREV_SQL runs right after each insert
-- batch).
-- provider / model ride along (min over the chain's rows — one chain is
-- ONE conversation under ONE provider type: the ext_llm_provider label
-- loaded from static.secret_config at agent start, plus the model id)
-- so the token tails can show WHICH type produced the consumption.
-- Serves GET /api/ai/threads/:threadId (the llm_usage block).
-- args:
--   chain_ids: value text[] — the thread's LLM context (chain) ids
SELECT chain_id AS context_id,
       n_requests::int,
       llm_ms::int,
       span_s::int,
       input_tokens::int,
       output_tokens::int,
       total_tokens::int,
       COALESCE(cache_tokens, 0)::int AS cache_tokens,
       (input_tokens - COALESCE(cache_tokens, 0))::int AS fresh_input_tokens,
       provider,
       model,
       first_at,
       last_at
  FROM (SELECT r.chain_id AS chain_id,
               count(*) AS n_requests,
               sum(r.duration_ms) AS llm_ms,
               EXTRACT(epoch FROM (max(r.created_at) - min(r.created_at)))
                 AS span_s,
               sum(r.input_tokens) AS input_tokens,
               sum(r.output_tokens) AS output_tokens,
               sum(r.total_tokens) AS total_tokens,
               sum(least(prev.input_tokens, r.input_tokens))
                 FILTER (WHERE prev.input_tokens IS NOT NULL
                          AND r.input_tokens IS NOT NULL) AS cache_tokens,
               min(r.provider) AS provider,
               min(r.model) AS model,
               (min(r.created_at) AT TIME ZONE 'Asia/Shanghai')::text AS first_at,
               (max(r.created_at) AT TIME ZONE 'Asia/Shanghai')::text AS last_at
          FROM llm.llm_requests r
          LEFT JOIN llm.llm_requests prev
                 ON prev.request_id = r.prev_request_id
         WHERE r.chain_id = ANY({{chain_ids}}::text[])
         GROUP BY r.chain_id) rollup
