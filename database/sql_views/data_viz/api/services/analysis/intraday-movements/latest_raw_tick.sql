-- Intraday Movements — latest raw intraday tick time for the benchmark on
-- the target date (step 2b: detect that the raw table has newer ticks
-- than the live tick table, switching to the on-the-fly fallback).
-- Serves GET /api/live-data/intraday-movements
-- args:
--   benchmark_code: value text — the benchmark index code
--   date: value date — the target trading date
SELECT time::text AS time FROM stats.index_intraday_5min WHERE code = {{benchmark_code}}::text AND date = {{date}}::date AND close IS NOT NULL ORDER BY time DESC LIMIT 1
