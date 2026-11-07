-- Benchmark Attribution — daily close + fractional daily return (LATERAL
-- self-join for the prev-day close) + trading amount for ONE benchmark
-- index. Drives the 1st plot in "Benchmark Attribution" mode: the user
-- clicks a date on this chart to set the as-of date for the per-industry
-- attribution bar charts below.
-- Serves GET /api/analysis/industry-attribution/benchmark-price.
-- args:
--   code: value text — the benchmark index code
SELECT
        ib.date,
        ib.close,
        ib.trading_amount,
        (ib.close - pb.close) / NULLIF(pb.close, 0) AS daily_return
       FROM stats.index_basic_stats ib
       LEFT JOIN LATERAL (
         SELECT close FROM stats.index_basic_stats
         WHERE code = ib.code AND date < ib.date
         ORDER BY date DESC LIMIT 1
       ) pb ON true
       WHERE ib.code = {{code}}::text
       ORDER BY ib.date ASC
