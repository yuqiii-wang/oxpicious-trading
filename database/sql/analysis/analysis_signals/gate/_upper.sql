-- ============================================================================
--  analysis_signals confirmation gate — SHARED machinery
--
--  Consumed by analyze.analysis_signals.gate.fetch_confirm: runs AFTER
--  a family file (<bucket>.sql) materialized its bucket rows into
--  pg_temp.gate_fx (ANALYZEd — every join below plans on real row
--  counts). Family-agnostic by design; the gate semantics live here
--  ONCE instead of 8 times.
--
--  Params:
--    $1  stat_month date[]             (the target months)
--    $2  min_confidence float8         (the family's CONF_FLOOR bar;
--                                       NULL = no floor)
--
--  Forecast-result rule per bucket (on the MIXED forecast_results
--  row): material reverse P (> 0.01) + mean reversal (dir_ave > 0) +
--  probability lift over base_rates + magnitude lift over the base
--  drift + the driving-factor confidence >= the family's floor ($2).
-- ============================================================================

WITH t AS (
    SELECT DISTINCT unnest($1::date[]) AS target_month
), code_thr AS (
    -- Per-code prior calibration over the PRIOR bucket-months only
    -- (windows pooled per side/period): the prior mean BASE
    -- composite (the calibration factor) + its quantiles (the rank
    -- floor) + the prior mean directional move (proven_dir tier).
    SELECT t.target_month, fx.code, fx.side, fx.period,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY fx.base_conf) AS q25,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY fx.base_conf) AS q50,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY fx.base_conf) AS q75,
           percentile_cont(0.9) WITHIN GROUP (ORDER BY fx.base_conf) AS q90,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY fx.base_conf) AS q95,
           AVG(fx.base_conf) AS mean_conf,
           AVG(fx.dir_ave) AS mean_dir_ave,
           COUNT(fx.base_conf)::bigint AS n
    FROM t JOIN gate_fx fx ON fx.stat_month < t.target_month
    GROUP BY t.target_month, fx.code, fx.side, fx.period
), gated AS (
    -- Attach the prior factor (the code's prior mean composite,
    -- neutral below CONF_PRIOR_MIN_POP) and the tier.
    SELECT fx.*,
           CASE WHEN c.n >= 10
                THEN c.mean_conf ELSE 0.2 END AS prior,
           CASE WHEN c.n >= 100
                     AND c.mean_conf >= 0.4
                THEN 2
                WHEN c.n >= 100
                     AND c.mean_dir_ave >= 0.01
                THEN 1 ELSE 0 END AS tier_pts
    FROM gate_fx fx
    JOIN t ON t.target_month = fx.stat_month
    LEFT JOIN code_thr c ON c.target_month = fx.stat_month
                        AND c.code = fx.code
                        AND c.side = fx.side
                        AND c.period = fx.period
), qp AS (
    -- The forecast-result rule (all conjuncts) on the bucket's MIXED
    -- row — one row per bucket — with the full composite confidence.
    -- NOTE: no sample-size / t-stat bars (removed 2026-09 — the
    -- streak-merge leaves pct-width buckets ~3-6 merged signals);
    -- occ / stdc still feed the confidence factors.
    SELECT *, (0.25 * (GREATEST(t, 0) / (GREATEST(t, 0) + 3.0)) + 0.25 * (1 - exp(-LEAST(GREATEST(sh, 0), 50) / 1.2)) + 0.2 * (1 - exp(-LEAST(GREATEST(lp, 0), 10) / 0.5)) + 0.15 * LEAST(GREATEST((COALESCE(mlr, 1.125) - 1.05) / (0.1499999999999999), 0), 1)) + 0.15 * prior AS confidence
    FROM gated
    WHERE rp > 0.01 AND dir_ave > 0
      AND (base_prob IS NULL OR rp > base_prob)
      AND (base_dir IS NULL OR dir_ave > base_dir)
      AND ($2::float8 IS NULL
           OR (0.25 * (GREATEST(t, 0) / (GREATEST(t, 0) + 3.0)) + 0.25 * (1 - exp(-LEAST(GREATEST(sh, 0), 50) / 1.2)) + 0.2 * (1 - exp(-LEAST(GREATEST(lp, 0), 10) / 0.5)) + 0.15 * LEAST(GREATEST((COALESCE(mlr, 1.125) - 1.05) / (0.1499999999999999), 0), 1)) + 0.15 * prior >= $2::float8)
), qual AS (
    SELECT stat_month, win, side, code, MAX(tier_pts) AS tier_pts
    FROM qp
    GROUP BY stat_month, win, side, code
), best AS (
    -- The confidence's period — the MIXED row (one qualifying row
    -- per bucket; DISTINCT ON keeps the pipeline shape generic).
    SELECT DISTINCT ON (stat_month, win, side, code)
           stat_month, win, side, code,
           period AS best_period, confidence, t, sh, lp, prior, mlr
    FROM qp
    ORDER BY stat_month, win, side, code, confidence DESC, period
)
SELECT q.stat_month, q.win, q.side,
       array_agg(q.code) AS codes,
       array_agg(q.confidence) AS confidences,
       array_agg(q.tier_pts) AS tier_pts,
       array_agg(q.baseline) AS baselines,
       array_agg(q.srank) AS ranks,
       array_agg(q.best_period) AS periods,
       array_agg(jsonb_build_object(
           't', round(q.t::numeric, 4),
           'sharpe', round(q.sh::numeric, 4),
           'lift_prob', round(q.lp::numeric, 4),
           'prior', round(q.prior::numeric, 4),
           'f_evidence', round((GREATEST(q.t, 0) /
               (GREATEST(q.t, 0) + 3.0))::numeric, 4),
           'f_efficiency', round((1 -
               exp(-LEAST(GREATEST(q.sh, 0), 50) /
               1.2))::numeric, 4),
           'f_consistency', round((1 -
               exp(-LEAST(GREATEST(q.lp, 0), 10) /
               0.5))::numeric, 4),
           'f_swing', round(LEAST(GREATEST(
               (COALESCE(q.mlr, 1.125) - 1.05) /
               0.1499999999999999, 0), 1)::numeric, 4)
       )::text) AS factors
FROM (
    SELECT b.stat_month, b.win, b.side, b.code, b.confidence,
           b.best_period, b.t, b.sh, b.lp, b.prior, b.mlr,
           ql.tier_pts,
           c.mean_conf AS baseline,
           CASE WHEN c.n >= 30 THEN
               CASE WHEN b.confidence >= c.q95 THEN 0.95 WHEN b.confidence >= c.q90 THEN 0.9 WHEN b.confidence >= c.q75 THEN 0.75 WHEN b.confidence >= c.q50 THEN 0.5 WHEN b.confidence >= c.q25 THEN 0.25
               ELSE 0.0 END
           END AS srank
    FROM best b
    JOIN qual ql ON ql.stat_month = b.stat_month
                AND ql.win = b.win AND ql.side = b.side
                AND ql.code = b.code
    LEFT JOIN code_thr c ON c.target_month = b.stat_month
                        AND c.code = b.code
                        AND c.side = b.side
                        AND c.period = b.best_period
) q
GROUP BY q.stat_month, q.win, q.side
