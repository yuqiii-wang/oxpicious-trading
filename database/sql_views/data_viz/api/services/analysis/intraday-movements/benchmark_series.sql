-- Intraday Movements — benchmark % change per 5-min tick from the live
-- tick table (top plot main line). The benchmark % is denormalized onto
-- every member row at each tick — MAX() per time collapses the copies and
-- ignores member rows whose benchmark bar was NULL at that tick (all
-- non-NULL copies are identical).
-- Serves GET /api/live-data/intraday-movements
-- args:
--   date: value date — the target trading date
--   benchmark_code: value text — the benchmark index code
SELECT
    time::text                                                    AS time,
    MAX(benchmark_price_pct_relative_prev_date_close)::float8      AS benchmark_price_pct
FROM live.sec_alloc_live_attribution
WHERE benchmark_code = {{benchmark_code}}::text
  AND date = {{date}}::date
GROUP BY time
ORDER BY time
