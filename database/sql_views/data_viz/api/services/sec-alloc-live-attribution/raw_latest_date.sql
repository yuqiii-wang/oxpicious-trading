-- Latest RAW intraday date for one benchmark — the date-resolution
-- fallback when the tick cache has no rows at all for the benchmark (the
-- on-demand compute then backfills it).
-- Serves GET /api/live-data/sec-alloc-live/attribution.
-- args:
--   code: value text — the benchmark index code
SELECT MAX(date) AS max_date
FROM stats.index_intraday_5min
WHERE code = {{code}}::text AND close IS NOT NULL
