-- Daily data check — analysis_daily step 8 (analysis.volatility):
-- analysis.volatility (sec_type = index) per study-10 code vs the
-- index bars frontier — the panel is study-index scoped (the codes
-- mirror analysis/volatility's config.STUDY_INDEX_CODES, the pipeline's
-- run scope, like the oracle_signals_coverage view) and STRICT (0-day):
-- the volatility signal family reads this panel and running it a day
-- behind left that family one bar behind (the 2026-11-18 tier
-- reordering existed precisely to fix that).
-- Serves analysis.volatility (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- row per stale code).
-- args: none
WITH frontier AS (
    SELECT b.code, MAX(b.date) AS frontier_date
    FROM stats.index_basic_stats b
    WHERE b.close IS NOT NULL
      AND b.code IN ('000001', '931743', '931160', '931071', '000015',
                     '930708', '931994', '931594', '000683', '000991')
    GROUP BY b.code
),
built AS (
    SELECT code, MAX(date) AS latest_date
    FROM analysis.volatility
    WHERE sec_type = 1
      AND code IN ('000001', '931743', '931160', '931071', '000015',
                   '930708', '931994', '931594', '000683', '000991')
    GROUP BY code
)
SELECT f.code::text AS code, c.latest_date, f.frontier_date
FROM frontier f
LEFT JOIN built c ON c.code = f.code
WHERE c.latest_date IS NULL OR c.latest_date < f.frontier_date
