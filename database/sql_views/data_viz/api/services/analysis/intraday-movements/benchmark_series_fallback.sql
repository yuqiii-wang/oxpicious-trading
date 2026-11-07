-- Intraday Movements — fallback benchmark % change per 5-min tick,
-- computed on-the-fly from the raw intraday table + previous trading
-- day's close. Used when the live tick table hasn't been populated for
-- the target date yet (e.g. the live pipeline lags behind the raw data
-- ingestion). The value is a FRACTION (not percent): matches the live
-- tables' scale convention — the UI tooltip / yAxis formatters multiply
-- by 100 at render time, and the top-plot shade builder diffs benchmark
-- vs industry directly.
-- Serves GET /api/live-data/intraday-movements
-- args:
--   benchmark_code: value text — the benchmark index code
--   date: value date — the target trading date
SELECT
    i5.time::text AS time,
    -- FRACTION (not percent): matches the live tables' scale convention
    -- (benchmark_price_pct_relative_prev_date_close etc. are fractions). The
    -- UI tooltip / yAxis formatters multiply by 100 at render time, and the
    -- top-plot shade builder diffs benchmark vs industry directly — a ×100
    -- mismatch here puts the benchmark 100x above every industry curve,
    -- breaking the benchmark-centered shades during live market hours
    -- (the fallback is active exactly then: raw ticks ahead of live ticks).
    ROUND((i5.close - bs.prev_close) / NULLIF(bs.prev_close, 0), 6) AS benchmark_price_pct
FROM stats.index_intraday_5min i5
CROSS JOIN (
    SELECT close AS prev_close
    FROM stats.index_basic_stats
    WHERE code = {{benchmark_code}}::text
      AND date < {{date}}::date
      AND close IS NOT NULL
    ORDER BY date DESC
    LIMIT 1
) bs
WHERE i5.code = {{benchmark_code}}::text
  AND i5.date = {{date}}::date
  AND i5.close IS NOT NULL
ORDER BY i5.time
