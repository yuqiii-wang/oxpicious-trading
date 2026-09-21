-- 14_delay_ladder_audit.sql — the delay-ladder invariant, DB-side audit.
--
-- The writer enforces the invariant per month batch at write time (see
-- analyze.analysis_forecasts.writer: each bucket's forecast_results.delay
-- column must be contiguous 0..max and <= 5 — delay d exists iff the run
-- reached day d, per the 2026-09-21 incremental-anchor convention).
-- This file is the standalone audit for EXISTING rows: run it after a
-- rebuild (or anytime) to find buckets violating the invariant — e.g.
-- rows left over from a pre-migration convention (missing delay 0) or
-- fragmented ladders.
--
-- Usage: psql ... -f 14_delay_ladder_audit.sql  (read-only SELECTs).

-- 1. Buckets whose delay set has a GAP or misses 0 (violations).
--    Expect 0 rows.
WITH delays AS (
    SELECT forecast_id,
           min(delay) AS min_delay,
           max(delay) AS max_delay,
           count(DISTINCT delay) AS n_delays
    FROM analysis_forecasts.forecast_results
    WHERE period = 'mixed'
    GROUP BY forecast_id
)
SELECT i.bucket, i.sec_type, date_trunc('month', i.stat_month)::date AS stat_month,
       count(*) AS violating_buckets,
       min(d.min_delay) AS min_min_delay, max(d.max_delay) AS max_max_delay
FROM delays d
JOIN analysis_forecasts.forecast_identities i ON i.forecast_id = d.forecast_id
WHERE d.min_delay != 0                          -- no delay-0 anchor
   OR d.max_delay - d.min_delay + 1 != d.n_delays  -- hole in the ladder
GROUP BY i.bucket, i.sec_type, stat_month
ORDER BY i.bucket, i.sec_type, stat_month DESC;

-- 2. Delay-rung coverage: buckets by their max delay (the ladder reach
--    distribution — how far the bucket's streaks actually got).
SELECT max_delay, count(*) AS buckets
FROM (
    SELECT forecast_id, max(delay) AS max_delay
    FROM analysis_forecasts.forecast_results
    WHERE period = 'mixed'
    GROUP BY forecast_id
) t
GROUP BY max_delay
ORDER BY max_delay;

-- 3. Per-rung sample sizes (mixed rows): the decay rule reads means over
--    these n — upper rungs are small by construction (conditioned on the
--    streak having lasted delay + 1 days).
SELECT delay,
       count(*) AS rows,
       min(occurrence_count) AS min_n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY occurrence_count) AS median_n,
       max(occurrence_count) AS max_n
FROM analysis_forecasts.forecast_results
WHERE period = 'mixed'
GROUP BY delay
ORDER BY delay;
