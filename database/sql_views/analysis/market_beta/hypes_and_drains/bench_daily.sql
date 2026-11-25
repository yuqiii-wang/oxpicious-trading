-- Hypes & drains step (analysis.market_beta internal pipeline) —
-- per-(benchmark, date) benchmark legs: close + trading_amount. The LAG
-- returns (1d + per-period Nd) are computed in pandas afterwards. Rows
-- keep close IS NOT NULL (the former CTE's WHERE) so the row space — and
-- thus every LAG window position — stays identical.
-- args:
--   benchmark_codes: value text[] — the broad-market benchmark codes
--   min_date: value date — fetch floor (inclusive). Incremental mode
--     passes the warm-up floor (the LOOKBACK_TRADING_DAYS-th broad-market
--     trading date before the earliest missing target date, minus the
--     suspension margin); force mode passes 1900-01-01 so the predicate
--     matches every row and the row space is unchanged.
    SELECT code AS benchmark_code,
           extract(epoch from date)::float8 AS date,
           close::float8                    AS close,
           trading_amount::float8           AS trading_amount
    FROM stats.index_basic_stats
    WHERE code = ANY({{benchmark_codes}}::text[])
      AND close IS NOT NULL
      AND date >= {{min_date}}::date
    ORDER BY code, date
