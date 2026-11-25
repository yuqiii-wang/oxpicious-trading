-- Daily data check — analysis_daily step 6 (analysis.options):
-- analysis.options_spot_daily table-wide max(date) vs the
-- stats.options_terms x stats.options_settlement frontier (the
-- fetch_missing_spot_dates source the module itself uses — table-wide
-- here; the per-pair module gate for non-analyzable products stays OUT
-- of this check so a gated product never false-alarms). 1-day slack
-- for the settlement files' late-afternoon publish; beyond it the
-- nightly options pass stopped. (options_vol_index and the per-expiry
-- family tables ride the same nightly run — the spot table is its
-- canary.)
-- Serves analysis.options (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- table-wide row when stale).
-- args: none
WITH frontier AS (
    SELECT MAX(t.date) AS frontier_date
    FROM stats.options_terms t
    JOIN stats.options_settlement s
      ON s.date = t.date AND s.contract_code = t.contract_code
),
built AS (
    SELECT MAX(date) AS latest_date
    FROM analysis.options_spot_daily
)
SELECT NULL::text AS code, b.latest_date, f.frontier_date
FROM frontier f CROSS JOIN built b
WHERE b.latest_date IS NULL
   OR b.latest_date < f.frontier_date - INTERVAL '1 day'
