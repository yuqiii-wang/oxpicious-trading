-- weighted_available — the trading-amount weights are computable for the
-- (benchmark, date): a resolved prev date exists AND at least one eligible
-- member carries a REAL prev-day amount. While FALSE the UI disables the
-- "By Trading Amt" toggle. Serves
-- GET /api/live-data/sec-alloc-live/attribution.
-- args:
--   benchmark_code: value text — the benchmark index code
--   date: value text — tick date (YYYY-MM-DD)
WITH universe AS (
    SELECT code FROM stats.sec_classification
    WHERE is_active = TRUE
      AND type IN ('index', 'etf')
      AND industry_id IS NOT NULL AND industry_id <> ''
      AND industry_id NOT LIKE 'BROAD_%'
      AND code <> {{benchmark_code}}::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < {{date}}::date
      AND close IS NOT NULL
)
SELECT EXISTS (
    SELECT 1
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.trading_amount IS NOT NULL AND b.trading_amount::text <> 'NaN'
      AND b.code IN (SELECT code FROM universe)
) AS weighted_available
