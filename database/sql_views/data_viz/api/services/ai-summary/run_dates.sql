-- Summary History phase 1 — the strip's date buckets, most recent day
-- first. ONE ROW PER RUN: the three-graph chain's threads group under the
-- chain key (COALESCE(super_thread_id, thread_id) — legacy runs are their
-- own key). Statuses ride COUNT ... FILTER (WHERE ...) — no CASE.
-- Serves GET /api/ai/summary-runs/dates.
-- args:
--   where_clause: fragment — the summaryRunFilterConds WHERE text / ""
--     (caller-built; carries the caller's $n params)
--   limit: fragment — clamped run-date bucket count (caller-built literal)
SELECT ((s.created_at AT TIME ZONE 'GMT')::date)::text  AS run_date,
       COUNT(DISTINCT COALESCE(s.super_thread_id, s.thread_id))::int
                                                         AS runs_total,
       COUNT(*)::int                                     AS steps_total,
       COUNT(*) FILTER (WHERE s.status = 'completed')::int AS steps_completed,
       COUNT(*) FILTER (WHERE s.status = 'failed')::int    AS steps_failed,
       COUNT(*) FILTER (WHERE s.status = 'skipped')::int   AS steps_skipped
  FROM llm.deep_agent_states s
  {{where_clause}}
 GROUP BY 1
 ORDER BY 1 DESC
 LIMIT {{limit}}
