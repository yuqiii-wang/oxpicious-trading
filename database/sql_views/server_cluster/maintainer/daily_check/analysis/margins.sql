-- Daily data check — analysis_daily step 4 (analysis.margins):
-- analysis.margin_tech_stats max(date) per sec_type (etf, stock — the
-- two universes the pipeline builds from; the index/industry rows are
-- derived aggregates of the same dates) vs that sec_type's
-- stats.{etf,stock}_liquidity_margin frontier — the
-- detect_missing_dates source the pipeline itself uses. A lagging
-- sec_type means the nightly margin pass stopped for it. STRICT
-- (0-day): the build fills from the liquidity dates in the same
-- nightly pass, compared at 07:00 the morning after.
-- Serves analysis.margins (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- row per lagging sec_type).
-- args: none
SELECT *
FROM (
    SELECT 'etf'::text AS code,
           (SELECT MAX(date) FROM analysis.margin_tech_stats
            WHERE sec_type = 0) AS latest_date,
           (SELECT MAX(date) FROM stats.etf_liquidity_margin) AS frontier_date
    UNION ALL
    SELECT 'stock',
           (SELECT MAX(date) FROM analysis.margin_tech_stats
            WHERE sec_type = 2),
           (SELECT MAX(date) FROM stats.stock_liquidity_margin)
) t
WHERE t.latest_date IS NULL OR t.latest_date < t.frontier_date
