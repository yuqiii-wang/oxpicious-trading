-- Hypes & Drains — the benchmark's full daily close + fractional
-- close-to-close return (LAG window) + trading amount; the service
-- derives the daily return map that feeds the shared-portfolio identity.
-- Same source as BenchmarkPriceChart (stats.index_basic_stats).
-- Serves GET /api/analysis/industry-hypes-and-drains.
-- args:
--   code: value text — the benchmark index code
    SELECT
        ib.date,
        ib.close,
        (ib.close - LAG(ib.close) OVER w)
          / NULLIF(LAG(ib.close) OVER w, 0) AS daily_return,
        ib.trading_amount
    FROM stats.index_basic_stats ib
    WHERE ib.code = {{code}}::text
    WINDOW w AS (ORDER BY ib.date)
    ORDER BY ib.date
