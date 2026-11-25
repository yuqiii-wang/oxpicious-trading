-- Non-daily check — oracle_signals coverage vs the action frontier:
-- each study index's newest placeable action (a 5-bar-window candidate
-- needs its 2 right-side bars, and the one-action selection can place
-- the frontier unit right up to the bars frontier) versus the newest
-- action row the oracle table actually carries. The DAILY incremental
-- (2026-11-21 — the former weekly tier + gate retired) keeps the built
-- trail within a few bars of the frontier; a code beyond the 20-day
-- tolerance is genuinely stale (the nightly fire stopped writing —
-- check the arrangement board / the service_audit trail).
-- Serves analysis.oracle_signals (NonDailyOps,
-- server_cluster/monitor/non_daily.py — the db_universe_coverage check;
-- one row per stale code).
-- args: none
--   (the 10 study indices mirror analysis/oracle_signals/config.py's
--   STUDY_INDEX_CODES — the pipeline's run scope)
WITH frontier AS (
    SELECT b.code, MAX(b.date) AS frontier_date
    FROM stats.index_basic_stats b
    WHERE b.code IN ('000001', '931743', '931160', '931071', '000015',
                     '930708', '931994', '931594', '000683', '000991')
      AND b.close IS NOT NULL
    GROUP BY b.code
),
coverage AS (
    SELECT code, MAX(date) AS built_date
    FROM analysis.oracle_signals
    GROUP BY code
)
SELECT f.code, f.frontier_date, c.built_date
FROM frontier f
LEFT JOIN coverage c ON c.code = f.code
WHERE c.built_date IS NULL
   OR c.built_date < f.frontier_date - INTERVAL '20 days'
