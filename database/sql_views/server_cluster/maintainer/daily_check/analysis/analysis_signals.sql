-- Daily data check — analysis_daily step 9 (analysis.analysis_signals):
-- the mov_rsi family (signal_type = 0 — the tier's value source, the
-- family whose horizon IS the price history) in
-- analysis_signals.history_signals, TABLE-WIDE max(date) vs the index
-- bars frontier. history_signals is a sparse EVENT table (one row per
-- qualifying episode's first day — a code can go months without a
-- trigger), so a PER-CODE max(date) would false-alarm forever on
-- quiet codes; the full-universe nightly run virtually always records
-- SOME event on a trading day, which makes the table-wide max the
-- canary that "the signals tier wrote last night".
-- STRICT (0-day) vs the frontier date: the chain runs builds BEFORE
-- analysis, so the tier sees the frontier bars and its events ride the
-- frontier date (observed daily, 2026-10-26..11-19). The former
-- 1-CALENDAR-day slack masked ANY single-night stop — the 2026-11-20
-- gap read "loaded" all weekend (11-19 >= 11-20 - 1 day) because the
-- calendar slack spans the freeze: the bars frontier stays at Friday
-- until Monday's bars land, so a Friday-night stop cannot alarm on
-- calendar slack. A PARTIAL frontier day (a coverage stub, see
-- data_viz/index_daily) reads stale here too — the honest verdict: the
-- tier has nothing for that day.
-- Serves analysis.analysis_signals (DailyDataCheckJob,
-- server_cluster/maintainer/daily_check.py — zero rows = loaded; one
-- table-wide row when stale).
-- args: none
WITH frontier AS (
    SELECT MAX(date) AS frontier_date
    FROM stats.index_basic_stats
    WHERE close IS NOT NULL
),
built AS (
    SELECT MAX(date) AS latest_date
    FROM analysis_signals.history_signals
    WHERE sec_type = 1
      AND signal_type = 0
)
SELECT NULL::text AS code, b.latest_date, f.frontier_date
FROM frontier f CROSS JOIN built b
WHERE b.latest_date IS NULL
   OR b.latest_date < f.frontier_date
