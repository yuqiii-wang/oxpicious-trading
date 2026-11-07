-- Intraday Movements — distinct dates available for ONE benchmark (union
-- of raw intraday bars + live tick rows), newest first. Drives the page's
-- date selector.
-- Serves GET /api/live-data/intraday-movements/dates
-- args:
--   benchmark_code: value text — the benchmark to list available dates for
SELECT DISTINCT date::text AS date
FROM (
    SELECT date
    FROM stats.index_intraday_5min
    WHERE code = {{benchmark_code}}::text AND close IS NOT NULL
    UNION
    SELECT date
    FROM live.sec_alloc_live_attribution
    WHERE benchmark_code = {{benchmark_code}}::text
) d
ORDER BY date DESC
