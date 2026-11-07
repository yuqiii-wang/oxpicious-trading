-- Intraday Movements — the distinct RAW intraday tick times for the
-- benchmark on the target date (step 3: filter ALL series to only these
-- ticks, excluding extended-hours ticks the benchmark doesn't have).
-- Serves GET /api/live-data/intraday-movements
-- args:
--   benchmark_code: value text — the benchmark index code
--   date: value date — the target trading date
SELECT DISTINCT time::text AS time FROM stats.index_intraday_5min WHERE code = {{benchmark_code}}::text AND date = {{date}}::date AND close IS NOT NULL ORDER BY time
