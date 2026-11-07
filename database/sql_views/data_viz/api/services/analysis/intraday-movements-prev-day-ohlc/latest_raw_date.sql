-- Intraday Movements prev-day OHLC — latest date with raw intraday bars
-- for ONE benchmark (resolves the live date the same way the main
-- intraday-movements endpoint does when the request omits ?date=).
-- Serves GET /api/live-data/intraday-movements/prev-day-ohlc
-- args:
--   benchmark_code: value text — the benchmark index code
SELECT MAX(date) AS max_date FROM stats.index_intraday_5min
       WHERE code = {{benchmark_code}}::text AND close IS NOT NULL
