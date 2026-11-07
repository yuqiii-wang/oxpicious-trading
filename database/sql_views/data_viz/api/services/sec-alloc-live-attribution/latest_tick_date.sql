-- Latest tick date for one benchmark in the live.sec_alloc_live_attribution
-- rolling cache. Serves GET /api/live-data/sec-alloc-live/attribution.
-- args:
--   benchmark_code: value text — the benchmark index code
SELECT MAX(date) AS max_date
FROM live.sec_alloc_live_attribution
WHERE benchmark_code = {{benchmark_code}}::text
