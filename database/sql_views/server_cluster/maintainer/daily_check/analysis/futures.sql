-- Daily data check — analysis_daily step 5 (analysis.futures):
-- analysis.futures_ext table-wide max(date) vs stats.futures_identity
-- (the fetch_futures_identity_dates frontier the module itself uses —
-- table-wide here; the per-product module gate for the offshore CN/MCA
-- pairs stays OUT of this check so a gated pair never false-alarms).
-- A gap means the nightly basis/correlation pass stopped writing.
-- STRICT (0-day): the build fills from the identity dates in the same
-- nightly pass, compared at 07:00 the morning after.
-- Serves analysis.futures (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- table-wide row when stale).
-- args: none
SELECT *
FROM (
    SELECT NULL::text AS code,
           (SELECT MAX(date) FROM analysis.futures_ext) AS latest_date,
           (SELECT MAX(date) FROM stats.futures_identity) AS frontier_date
) t
WHERE t.latest_date IS NULL OR t.latest_date < t.frontier_date
