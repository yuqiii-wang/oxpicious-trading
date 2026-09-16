-- ============================================================================
--  analysis_signals confirmation gate — family mov_gap
--
--  The family's BUCKET ROWS as one standalone SQL (one metric one sql):
--  loaded by analyze.analysis_signals.gate.fetch_confirm, which runs
--  this as `CREATE TEMP TABLE gate_fx ON COMMIT DROP AS <this file>` +
--  ANALYZE, then applies the shared machinery (_upper.sql) on top —
--  real row counts for every join, no estimate-blind planning.
--
--  Params:
--    $1  sec_type                      ('index' | 'etf' | 'stock')
--    $2  stat_month date[]             (the target months)
--
--  Family slice (MIRRORED CONSTANTS — keep in sync with
--  analyze/analysis_signals/config.py; regenerate this file with
--  temp_scripts/_gen_gate_sql.py after changing any of them):
--  config filter = m.pct = 1                    (GAP_PCT)
--
--  Output: one row per bucket — (stat_month, win, side, code,
--  sec_type, period, rp, dir_ave, occ, stdc, mlr, base_dir, base_prob,
--  t, sh, lp, base_conf).
-- ============================================================================

WITH bp AS (
    -- The family's bucket rows joined to their MIXED result row
    -- (inside the (forecast_id, period) PK probe) + the blended
    -- base_rates reference, in the bucket side's direction. The
    -- motivation tables are code-partitioned; the code-equality
    -- predicates keep every join on the code-leading PKs.
    SELECT i.stat_month, m.gap_window AS win, m.side, i.code, i.sec_type,
           fr.period, fr.reverse_prob::float8 AS rp,
           CASE WHEN m.side IN ('top', 'upper')
                THEN -fr.ave_change::float8
                ELSE fr.ave_change::float8 END AS dir_ave,
           fr.occurrence_count::float8 AS occ,
           fr.std_change::float8 AS stdc,
           fr.max_low_change_ratio::float8 AS mlr,
           CASE WHEN m.side IN ('top', 'upper')
                THEN -br.base_ave_change::float8
                ELSE br.base_ave_change::float8 END AS base_dir,
           CASE WHEN m.side IN ('top', 'upper')
                THEN br.base_down_prob::float8
                ELSE br.base_up_prob::float8 END AS base_prob
    FROM analysis_forecasts.forecast_identities i
    JOIN analysis_forecasts.mov_gap m
      ON m.forecast_id = i.forecast_id AND m.code = i.code
    JOIN analysis_forecasts.forecast_results fr
      ON fr.forecast_id = m.forecast_id
     AND fr.period = 'mixed'
    LEFT JOIN analysis_forecasts.base_rates br
      ON br.sec_type = i.sec_type AND br.code = i.code
     AND br.stat_month = i.stat_month
     AND br.period = 'mixed'
    WHERE i.sec_type = $1 AND i.bucket = 'mov_gap'
      AND m.pct = 1
      AND i.stat_month <= (SELECT MAX(x) FROM unnest($2::date[]) x)
), fx0 AS (
    -- The driving factors: significance (t), per-observation
    -- efficiency (sharpe) and hit-rate lift over the base rate —
    -- all in the bucket side's direction, all horizon-free.
    SELECT bp.*,
           bp.dir_ave * sqrt(bp.occ) / NULLIF(bp.stdc, 0) AS t,
           bp.dir_ave / NULLIF(bp.stdc, 0) AS sh,
           bp.rp - COALESCE(bp.base_prob, 0) AS lp
    FROM bp
)
    -- The BASE composite (evidence + efficiency + consistency +
    -- swing).
SELECT fx0.*,
       (0.25 * (GREATEST(t, 0) / (GREATEST(t, 0) + 3.0)) + 0.25 * (1 - exp(-LEAST(GREATEST(sh, 0), 50) / 1.2)) + 0.2 * (1 - exp(-LEAST(GREATEST(lp, 0), 10) / 0.5)) + 0.15 * LEAST(GREATEST((COALESCE(mlr, 1.125) - 1.05) / (0.1499999999999999), 0), 1)) AS base_conf
FROM fx0
