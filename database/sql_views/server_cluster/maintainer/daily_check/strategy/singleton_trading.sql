-- Daily data check — strategy_daily step 1 (strategy.singleton_trading):
-- the nightly mixed portfolio's latest ACTIVE run end_date per code vs
-- that code's own index bars frontier. strategy_identity carries one
-- row per strategy execution on one code; end_date is "the OHLC period
-- the strategy is run over" (df.date.max() — the runner pins the last
-- OHLC date) and is_active marks the latest run per (strategy_name,
-- sec_type, code).
--
-- Scoped to the CURRENT nightly portfolio's strategy_name (the
-- portfolio_name() form of the tier's --algo family list, carried as
-- the config's strategy_name param — update the two together when the
-- tier args change): abandoned family selections stay is_active in
-- strategy_identity forever, and MAX() across all portfolio:* names
-- would nag codes the current portfolio never covered (old-name-only
-- codes, out-of-universe codes) — noise. The JOIN direction runs
-- built → frontier, so only codes the current portfolio HAS covered
-- are judged: when the nightly run stops, their end_dates freeze while
-- their bars advance and the rows appear; a code outside the run's
-- universe never nags. 1-day slack for the runner's analysis-horizon
-- cap (the backtest never runs past the analysis build's last date —
-- strategy/_common/runner.py).
-- Serves strategy.singleton_trading (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- row per stale code).
-- args:
--   strategy_name: value text — the nightly mixed portfolio's
--     strategy_name (the config's params carry it; update it together
--     with the tier's --algo family list)
WITH frontier AS (
    SELECT b.code, MAX(b.date) AS frontier_date
    FROM stats.index_basic_stats b
    WHERE b.close IS NOT NULL
    GROUP BY b.code
),
built AS (
    SELECT i.code, MAX(i.end_date) AS latest_date
    FROM strategy.strategy_identity i
    WHERE i.strategy_name = {{strategy_name}}::text
      AND i.sec_type = 'index'   -- the strategy schema's TEXT sec_type
      AND i.is_active
    GROUP BY i.code
)
SELECT b.code::text AS code, c.latest_date, b.frontier_date
FROM built c
JOIN frontier b ON b.code = c.code
WHERE c.latest_date < b.frontier_date - INTERVAL '1 day'
