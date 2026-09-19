-- ============================================================================
--  Table: analysis_forecasts.base_rates
--
--  UNCONDITIONAL same-window reference for the forecast analysis — the
--  base rate every bucket stat in analysis_forecasts.forecast_results
--  is meant to be read against (lift):
--
--    per (sec_type, code, stat_month, period), over the code's OWN
--    trailing 5-year window (stat_month - 5y, stat_month] — the same
--    window, price space and forward-change definition as the buckets,
--    but over ALL of the code's trading days in the window (not just
--    the extreme days):
--
--      base_ave_change — mean n-day forward fractional change over all
--                        window days (bucket ave_change − base = edge)
--      base_down_prob  — P(n-day change < −threshold) — the
--                        base rate for top/upper-side reverse_prob
--      base_up_prob    — P(n-day change > +threshold) — the
--                        base rate for bottom/lower-side reverse_prob
--      base_count      — window days with a valid n-day forward change
--                        (denominator of all three)
--      threshold — the SAME (fixed 1%) reversal bar the bucket
--                        rows of this (code, stat_month, period) use
--                        (k_n · σ of the code's window n-day forward
--                        changes; fixed 0.01 fallback/legacy), so lift
--                        stays in one scale
--
--  A fixed ±1% reversal threshold is nearly saturated at the 20d/60d
--  horizons (close to the unconditional rate)
--  (study 2026-09, temp_scripts/study_threshold.py) keeps the
--  probabilities comparable across horizons; comparing reverse_prob
--  against base_down_prob / base_up_prob at the SAME bar is what makes
--  the per-horizon reversal probabilities interpretable.
--
--  Full-window gate: identical to the mov_* tables (a code enters a
--  stat_month only once its own history strictly precedes the window
--  start). One row per (code, period); rows are emitted only where
--  base_count > 0. Populated by python -m analyze.analysis_forecasts
--  alongside the mov_* tables.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.base_rates (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    stat_month      DATE         NOT NULL,  -- completed month-end; window (stat_month - 5y, stat_month]
    period          TEXT         NOT NULL,  -- 'next' | '5d' | '20d' | '60d' | 'mixed'

    -- Trailing calendar window the rates were computed over (recorded
    -- build parameter): '5y' = (stat_month - 5y, stat_month]
    lookback_period TEXT         NOT NULL DEFAULT '5y',

    base_count      BIGINT,                 -- window days with a valid n-day forward change (denominator)
    base_ave_change NUMERIC(10,6),          -- mean n-day forward fractional change over ALL window days
    base_down_prob  NUMERIC(8,6),           -- P(n-day change < −threshold) over ALL window days (top/upper-side reverse_prob base)
    base_up_prob    NUMERIC(8,6),           -- P(n-day change > +threshold) over ALL window days (bottom/lower-side reverse_prob base)
    threshold NUMERIC(8,6) NOT NULL DEFAULT 0.01,  -- the (fixed 1%) reversal bar the probs (and the bucket rows) use

    CONSTRAINT pk_base_rates PRIMARY KEY (sec_type, code, stat_month, period)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — same convention as the
-- mov_* motivation tables
-- (database/sql/00_partition_utils.sql; children _p00.._p15).
SELECT public.create_hash_partitions('analysis_forecasts', 'base_rates', 16);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.base_rates IS 'Unconditional same-window base rates for the forecast analysis: per (sec_type, code, stat_month, period) the mean n-day forward fractional change, P(change < −threshold) and P(change > +threshold) over ALL of the code''s trading days in the trailing 5-year window ending at stat_month (not just the extreme bucket days), plus the valid-day count and the SAME FIXED 1% threshold the bucket rows use (lift stays in one scale). Reference for reading forecast_results.ave_change / reverse_prob as lift. Same window / price space / full-window gate as the mov_* tables. Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.base_rates.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_forecasts.base_rates.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_forecasts.base_rates.stat_month IS 'Completed month-end date. The rates are computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.base_rates.period IS 'Forward horizon period: ''next'' (next-day), ''5d'' (5 trading days), ''20d'' (20 trading days), ''60d'' (60 trading days), or ''mixed'' — the FIXED-weight blend of the four horizon rows (5d 0.50 / next 0.30 / 20d 0.15 / 60d 0.05; weights renormalized over the horizons with valid data, base_count = the MIN valid count over those legs, threshold = the full-weight mean of the four bars) — the unconditional reference of the bucket rows'' blended period=''mixed'' profile. PK member.';

-- ----------------------------------------------------------------------------
--  Migration (2026-09 mixed period): every (sec_type, code, stat_month)
--  gains the weight-blended 'mixed' base-rate row — the FIXED-weight
--  blend of its four horizon rows at 5d 0.50 / next 0.30 / 20d 0.15 /
--  60d 0.05 (the MIXED_HORIZON_WEIGHTS of
--  analyze.analysis_forecasts.config) — the unconditional reference the
--  bucket rows' blended 'mixed' period is read against (lift stays in
--  one scale; the analysis_signals gate consumes both). Idempotent:
--  keys already carrying a mixed row are skipped; the forecasts writer
--  emits the row natively from its next run (or --force rebuild).
--
--  Blend semantics (mirroring compute_base.compute_base_rate_rows):
--    base_ave_change / base_down_prob / base_up_prob — the weight mean
--        of the horizon values, weights renormalized over the horizons
--        with a positive base_count;
--    base_count — MIN over those legs (the blend is only as
--        well-observed as its weakest leg), 0 when none;
--    threshold — the full-weight mean of the four bars.
-- ----------------------------------------------------------------------------
WITH w(period, wt) AS (
    VALUES ('next', 0.30::float8), ('5d', 0.50::float8),
           ('20d', 0.15::float8), ('60d', 0.05::float8)
), leg AS (
    SELECT b.sec_type, b.code, b.stat_month,
           w.wt,
           b.lookback_period,
           COALESCE(b.base_count, 0) AS cnt,
           b.base_ave_change::float8 AS ave,
           b.base_down_prob::float8 AS dn,
           b.base_up_prob::float8 AS up,
           b.threshold::float8 AS thr
    FROM analysis_forecasts.base_rates b
    JOIN w ON w.period = b.period
    WHERE b.period <> 'mixed'
), agg AS (
    SELECT l.sec_type, l.code, l.stat_month,
           (ARRAY_AGG(l.lookback_period))[1] AS lookback_period,
           SUM(l.wt) FILTER (WHERE l.ave IS NOT NULL) AS w_stat,
           SUM(l.wt * l.ave) AS s_ave,
           SUM(l.wt * l.dn) AS s_dn,
           SUM(l.wt * l.up) AS s_up,
           MIN(l.cnt) FILTER (WHERE l.cnt > 0) AS cnt_min,
           SUM(l.wt * l.thr) AS thr_mix
    FROM leg l
    GROUP BY l.sec_type, l.code, l.stat_month
)
INSERT INTO analysis_forecasts.base_rates
       (sec_type, code, stat_month, period, lookback_period,
        base_count, base_ave_change, base_down_prob, base_up_prob,
        threshold)
SELECT a.sec_type, a.code, a.stat_month, 'mixed', a.lookback_period,
       COALESCE(a.cnt_min, 0),
       a.s_ave / a.w_stat,
       a.s_dn / a.w_stat,
       a.s_up / a.w_stat,
       a.thr_mix
FROM agg a
WHERE NOT EXISTS (
    SELECT 1 FROM analysis_forecasts.base_rates m
    WHERE m.sec_type = a.sec_type AND m.code = a.code
      AND m.stat_month = a.stat_month AND m.period = 'mixed'
);
COMMENT ON COLUMN analysis_forecasts.base_rates.base_count IS 'Number of the code''s window days with a valid n-trading-day forward change — the denominator of base_ave_change / base_down_prob / base_up_prob.';
COMMENT ON COLUMN analysis_forecasts.base_rates.base_ave_change IS 'Mean n-trading-day forward fractional change over ALL window days with a valid n-day forward change. Baseline for forecast_results.ave_change (bucket mean − base = conditional edge).';
COMMENT ON COLUMN analysis_forecasts.base_rates.base_down_prob IS 'P(n-day forward change < −threshold) over ALL window days with a valid n-day forward change. Baseline for the reverse_prob of top (RSI) and upper (Bollinger) buckets.';
COMMENT ON COLUMN analysis_forecasts.base_rates.base_up_prob IS 'P(n-day forward change > +threshold) over ALL window days with a valid n-day forward change. Baseline for the reverse_prob of bottom (RSI) and lower (Bollinger) buckets.';
COMMENT ON COLUMN analysis_forecasts.base_rates.threshold IS 'The fractional reversal bar the base probs (and the matching bucket rows of the same code/stat_month/period) are computed at: the FIXED 1% (0.01) bar since 2026-09-08 — the probs read as plain P(> 1%) / P(< -1%) against the period-end close. The earlier adaptive k_n · σ bar (study 2026-09; next 0.5, 5d 0.75, 20d 1.0, 60d 1.0) is disabled in the forecasts config (REVERSE_THRESHOLD_MODE = "fixed"), kept only as a flip-back option.';
COMMENT ON COLUMN analysis_forecasts.base_rates.lookback_period IS 'Recorded build parameter: the trailing calendar window the rates were computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';
