-- Intraday Movements — probe for pair-grain 'index' rows in the
-- cross-stats summary rollup. Non-empty → build the benchmark list from
-- the millisecond summary variant; empty (fresh DB before the first
-- builds.cross_stats run) → the full-table DISTINCT scan variant.
-- Serves GET /api/live-data/intraday-movements/benchmarks
-- args: (none)
SELECT 1 AS ok FROM stats.cross_stats_code_summary WHERE sec_type = 'index' LIMIT 1
