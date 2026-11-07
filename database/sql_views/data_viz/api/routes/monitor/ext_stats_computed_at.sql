-- External Resources — when the stats runner last refreshed (newest
-- computed_at in the trailing window; surfaced as the response's
-- computedAt).
-- Serves GET /api/monitor/ext-resources/stats.
-- args:
--   days: value int — trailing window length in days (clamped 1-365)
SELECT max(computed_at) AS computed_at
       FROM monitor.external_resource_stats
       WHERE date >= (CURRENT_DATE - ({{days}}::int - 1))