-- Hypes & drains step (analysis.market_beta internal pipeline) —
-- per-(benchmark, date) benchmark legs: close + trading_amount. The LAG
-- returns (1d + per-period Nd) are computed in pandas afterwards. Rows
-- keep close IS NOT NULL (the former CTE's WHERE) so the row space — and
-- thus every LAG window position — stays identical.
-- args:
--   benchmark_codes: value text[] — the broad-market benchmark codes
    SELECT code AS benchmark_code,
           extract(epoch from date)::float8 AS date,
           close::float8                    AS close,
           trading_amount::float8           AS trading_amount
    FROM stats.index_basic_stats
    WHERE code = ANY({{benchmark_codes}}::text[])
      AND close IS NOT NULL
    ORDER BY code, date
