-- Intraday Movements — latest date with raw intraday bars for ONE
-- benchmark (step 1 target-date resolution: prefer the RAW table so the
-- page always shows the most recent trading day even when the live tick
-- pipeline hasn't ingested that date yet).
-- Serves GET /api/live-data/intraday-movements
-- args:
--   benchmark_code: value text — the benchmark to resolve the latest date for
SELECT MAX(date) AS max_date FROM stats.index_intraday_5min WHERE close IS NOT NULL AND code = {{benchmark_code}}::text
