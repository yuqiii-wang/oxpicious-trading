-- Daily data check — analysis_daily step 3 (analysis.pe_and_dividends):
-- analysis.pe and analysis.dividends max(date) per sec_type vs that
-- sec_type's identity table (stats.{etf,index,stock,industry}_identity
-- — the SEC_TYPE_IDENTITY_TABLE frontier the module's own
-- _detect_missing_dates uses). A lagging row means the nightly
-- PE/dividend pass stopped for that sec_type. STRICT (0-day): both
-- build from the identity dates in the same nightly pass, compared at
-- 07:00 the morning after.
-- Serves analysis.pe_and_dividends (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- row per lagging (target, sec_type)).
-- args: none
SELECT *
FROM (
    SELECT 'pe:etf'::text AS code,
           (SELECT MAX(date) FROM analysis.pe WHERE sec_type = 0) AS latest_date,
           (SELECT MAX(date) FROM stats.etf_identity) AS frontier_date
    UNION ALL
    SELECT 'pe:index',
           (SELECT MAX(date) FROM analysis.pe WHERE sec_type = 1),
           (SELECT MAX(date) FROM stats.index_identity)
    UNION ALL
    SELECT 'pe:stock',
           (SELECT MAX(date) FROM analysis.pe WHERE sec_type = 2),
           (SELECT MAX(date) FROM stats.stock_identity)
    UNION ALL
    SELECT 'pe:industry',
           (SELECT MAX(date) FROM analysis.pe WHERE sec_type = 3),
           (SELECT MAX(date) FROM stats.industry_identity)
    UNION ALL
    SELECT 'dividends:etf',
           (SELECT MAX(date) FROM analysis.dividends WHERE sec_type = 0),
           (SELECT MAX(date) FROM stats.etf_identity)
    UNION ALL
    SELECT 'dividends:index',
           (SELECT MAX(date) FROM analysis.dividends WHERE sec_type = 1),
           (SELECT MAX(date) FROM stats.index_identity)
    UNION ALL
    SELECT 'dividends:stock',
           (SELECT MAX(date) FROM analysis.dividends WHERE sec_type = 2),
           (SELECT MAX(date) FROM stats.stock_identity)
    UNION ALL
    SELECT 'dividends:industry',
           (SELECT MAX(date) FROM analysis.dividends WHERE sec_type = 3),
           (SELECT MAX(date) FROM stats.industry_identity)
) t
WHERE t.latest_date IS NULL OR t.latest_date < t.frontier_date
