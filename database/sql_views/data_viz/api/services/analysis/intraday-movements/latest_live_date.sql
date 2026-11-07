-- Intraday Movements — latest date in the LIVE tick table for ONE
-- benchmark (step 1 target-date fallback when the raw intraday table has
-- no data at all).
-- Serves GET /api/live-data/intraday-movements
-- args:
--   benchmark_code: value text — the benchmark to resolve the latest date for
SELECT MAX(date) AS max_date FROM live.sec_alloc_live_attribution WHERE benchmark_code = {{benchmark_code}}::text
