-- Service Monitor — DAILY RUN COUNTS of ONE pipeline service over
-- monitor.service_audit: one row per Shanghai-local day in the trailing
-- window, GAP-FILLED with 0 so the UI's curve shows quiet days instead
-- of skipping them. Day buckets follow Asia/Shanghai — the same local
-- day the UI formats (fmtDateTimeShanghai) and the logs/<date>/ tree
-- files by, not the DB session timezone.
-- Serves GET /api/monitor/stream/runs/daily (per-service curve; the
-- picker's service list is stream_runs_daily_services.sql).
-- args:
--   service: value text — the dotted module path to count
--   days: value int — trailing window length in days (clamped 1-365 at
--         the route; ends at today, Shanghai-local)
WITH bounds AS (
  SELECT (now() AT TIME ZONE 'Asia/Shanghai')::date AS today,
         ((now() AT TIME ZONE 'Asia/Shanghai')::date
            - ({{days}}::int - 1)) AS start_day
),
series AS (
  SELECT generate_series(b.start_day, b.today, interval '1 day')::date::text
                AS day
  FROM bounds b
),
counts AS (
  SELECT (sa.started_at AT TIME ZONE 'Asia/Shanghai')::date::text AS day,
         COUNT(*)::int AS run_count,
         COUNT(*) FILTER (WHERE NOT sa.is_normal_exit)::int AS abnormal_count
  FROM monitor.service_audit sa, bounds b
  WHERE sa.started_at >= (b.start_day AT TIME ZONE 'Asia/Shanghai')
    AND sa.service = {{service}}
  GROUP BY day
)
SELECT s.day,
       COALESCE(c.run_count, 0)::int      AS run_count,
       COALESCE(c.abnormal_count, 0)::int AS abnormal_count
FROM series s
LEFT JOIN counts c USING (day)
ORDER BY s.day