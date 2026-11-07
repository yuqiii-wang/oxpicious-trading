-- Intraday Movements — benchmark display name from stats.index_identity
-- (latest identity row for the benchmark code).
-- Serves GET /api/live-data/intraday-movements
-- args:
--   benchmark_code: value text — the benchmark index code
SELECT name FROM stats.index_identity WHERE code = {{benchmark_code}}::text ORDER BY date DESC LIMIT 1
