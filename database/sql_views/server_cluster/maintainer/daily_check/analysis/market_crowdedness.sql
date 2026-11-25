-- Daily data check — analysis_daily step 11
-- (analysis.market_crowdedness): analysis.market_crowdedness max(date)
-- per sec_type (1 = index pool, 3 = industry pool) vs the
-- stats.stock_liquidity_margin trading-day frontier (trading_amount >
-- 0 — the module's own clamp: dates outside the liquidity range can
-- never gain a row, pipeline.py's _filter_missing_dates). A lagging
-- sec_type means the nightly crowdedness pass stopped for it. STRICT
-- (0-day): the build fills from the liquidity dates in the same
-- nightly pass, compared at 07:00 the morning after.
-- Serves analysis.market_crowdedness (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- row per lagging sec_type).
-- args: none
SELECT *
FROM (
    SELECT 'index'::text AS code,
           (SELECT MAX(date) FROM analysis.market_crowdedness
            WHERE sec_type = 1) AS latest_date,
           (SELECT MAX(date) FROM stats.stock_liquidity_margin
            WHERE trading_amount > 0) AS frontier_date
    UNION ALL
    SELECT 'industry',
           (SELECT MAX(date) FROM analysis.market_crowdedness
            WHERE sec_type = 3),
           (SELECT MAX(date) FROM stats.stock_liquidity_margin
            WHERE trading_amount > 0)
) t
WHERE t.latest_date IS NULL OR t.latest_date < t.frontier_date
