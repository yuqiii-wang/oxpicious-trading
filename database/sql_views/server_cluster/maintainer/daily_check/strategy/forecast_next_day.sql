-- Daily data check — strategy_daily step 2 (strategy.forecast_next_day):
-- the forecast register's max(anchor_date) per study-10 code vs the
-- OHLCV close frontier. anchor_date is "the REAL bar date the forecast
-- is anchored on (the code's latest OHLCV close)" and the module's own
-- incremental rule is anchor == horizon (fetch_existing_anchors: a
-- code whose registered anchor matches its close horizon is skipped) —
-- so a lagging anchor means the nightly forecast pass stopped. The
-- study-10 codes mirror the run scope (STUDY_CODES, the tier's own
-- universe — the forecast page reads the same rows); 1-day slack for
-- the intra-chain ordering, beyond that the pass stopped.
-- Serves strategy.forecast_next_day (DailyDataCheckJob,
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
    SELECT code, MAX(anchor_date) AS latest_date
    FROM strategy.forecast_next_day
    WHERE sec_type = 1   -- smallint label code: 1 = index
      AND code IN ('000001', '931743', '931160', '931071', '000015',
                   '930708', '931994', '931594', '000683', '000991')
    GROUP BY code
)
SELECT f.code::text AS code, c.latest_date, f.frontier_date
FROM frontier f
LEFT JOIN built c ON c.code = f.code
WHERE c.latest_date IS NULL
   OR c.latest_date < f.frontier_date - INTERVAL '1 day'
