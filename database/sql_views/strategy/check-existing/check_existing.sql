-- Check if a strategy run already exists for the natural key — the latest
-- strategy_identity row for (strategy_name, sec_type, code), preferring
-- the is_active run then the highest seq_no. Also reports whether the
-- existing run is STALE: its end_date (the OHLC period the strategy was
-- run over — the run's last data day, the LDS) ends before the code's own
-- latest analysis row does. The reference is the analysis build's
-- mov_ave_spreads_detail horizon — the SAME cap the strategy runner trims
-- to (strategy/_common/runner.py) — because a rerun only ever extends the
-- run up to that horizon; basic_stats can run ahead of it (live partial
-- bars) without a rerun reaching further. Drives the UI's force-rerun
-- flow: a stale run reruns WITHOUT the confirmation modal (user-directed
-- 2026-11-25), only a fresh run asks first.
-- Serves GET /api/strategy/singleton/check.
-- args:
--   strategy_name: value text — algo name (binary) or portfolio name (mixed)
--   sec_type: value text — 'index' | 'etf' | 'stock'
--   code: value text — the traded security code (the table's own format)
--   sec_type_code: value smallint — the static.label_dim sec_type code
--     (LABEL_CODE.sec_type) matching the detail table's SMALLINT column
  SELECT si.seq_id, si.seq_no, si.start_date, si.end_date, si.scenario,
         si.status, si.fault_tolerance,
         m.analysis_latest_date,
         COALESCE(si.end_date IS NULL
                    OR si.end_date < m.analysis_latest_date, false) AS stale
    FROM strategy.strategy_identity si
    CROSS JOIN (SELECT max(d.date) AS analysis_latest_date
                  FROM analysis.mov_ave_spreads_detail d
                 WHERE d.sec_type = {{sec_type_code}}
                   AND d.code = {{code}}) m
   WHERE si.strategy_name = {{strategy_name}}
     AND si.sec_type = {{sec_type}}
     AND si.code = {{code}}
   ORDER BY si.is_active DESC, si.seq_no DESC
   LIMIT 1
