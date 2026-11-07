-- Benchmark Attribution — display name of ONE benchmark index (latest
-- stats.index_identity row per code; empty response falls back to the
-- code client-side).
-- Serves GET /api/analysis/industry-attribution/benchmark-price.
-- args:
--   code: value text — the benchmark index code
SELECT DISTINCT ON (code) code, name FROM stats.index_identity
       WHERE code = {{code}}::text ORDER BY code, date DESC
