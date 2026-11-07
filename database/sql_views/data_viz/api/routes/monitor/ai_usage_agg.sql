-- AI usage — LLM-request audit aggregated per (service, model, GMT date)
-- over a trailing window: request/failed counts, usage-block token sums
-- (extracted at write time — no JSONB parsing here) and whole-call
-- durations. Live aggregation (no computed table) — days x services x
-- models rows, small by construction; ix_llm_requests_date bounds the scan.
-- Serves GET /api/monitor/ai/usage.
-- args:
--   days: value int — trailing window length in days (clamped 1-365)
SELECT service, model, date::text AS date,
              COUNT(*)::int AS request_count,
              COUNT(*) FILTER (WHERE is_failed)::int AS failed_count,
              COALESCE(SUM(input_tokens), 0)::bigint  AS input_tokens,
              COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
              COALESCE(SUM(total_tokens), 0)::bigint  AS total_tokens,
              AVG(duration_ms)::float8 AS avg_duration_ms,
              MAX(duration_ms)::int    AS max_duration_ms
       FROM llm.llm_requests
       WHERE date >= (CURRENT_DATE - ({{days}}::int - 1))
       GROUP BY service, model, date
       ORDER BY date, service, model NULLS FIRST