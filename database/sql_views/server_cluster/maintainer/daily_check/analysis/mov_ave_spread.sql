-- Daily data check — analysis_daily step 2 (analysis.mov_ave_spread):
-- analysis.mov_ave_spreads_detail max(date) per sec_type vs that
-- sec_type's identity table (stats.{etf,index,stock,industry}_identity
-- — the SEC_TYPE_IDENTITY_TABLE frontier the pipeline's incremental
-- diff itself uses). A lagging sec_type means the nightly detail+rsi
-- pass stopped for it. STRICT (0-day): the detail builds from the
-- identity dates in the same nightly pass, compared at 07:00 the
-- morning after.
-- Serves analysis.mov_ave_spread (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- row per lagging sec_type).
-- args: none
WITH frontier AS (
    SELECT 0 AS sec_type, 'etf'::text AS sec_label,
           MAX(date) AS frontier_date
    FROM stats.etf_identity
    UNION ALL
    SELECT 1, 'index', MAX(date) FROM stats.index_identity
    UNION ALL
    SELECT 2, 'stock', MAX(date) FROM stats.stock_identity
    UNION ALL
    SELECT 3, 'industry', MAX(date) FROM stats.industry_identity
),
built AS (
    SELECT sec_type, MAX(date) AS latest_date
    FROM analysis.mov_ave_spreads_detail
    GROUP BY sec_type
)
SELECT f.sec_label AS code, b.latest_date, f.frontier_date
FROM frontier f
LEFT JOIN built b ON b.sec_type = f.sec_type
WHERE b.latest_date IS NULL OR b.latest_date < f.frontier_date
