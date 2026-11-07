-- External Resources — per-day external-request aggregates over the
-- trailing window (grain: service x status_code x date), read from the
-- COMPUTED monitor.external_resource_stats table (maintained by
-- python -m database.external_resource_stats).
-- Serves GET /api/monitor/ext-resources/stats.
-- args:
--   days: value int — trailing window length in days (clamped 1-365)
SELECT service, status_code, date, request_count, failed_count,
              avg_duration_ms, max_duration_ms
       FROM monitor.external_resource_stats
       WHERE date >= (CURRENT_DATE - ({{days}}::int - 1))
       ORDER BY date, service, status_code NULLS FIRST