-- Perf-attr chart — latest identity name for the benchmark code (first row
-- wins; the service reads benchNameRows[0]).
-- Serves GET /api/analysis/perf-attr/chart.
-- args:
--   code: value text — the benchmark code
SELECT DISTINCT ON (code) code, name FROM stats.index_identity WHERE code = {{code}}::text ORDER BY code, date DESC
