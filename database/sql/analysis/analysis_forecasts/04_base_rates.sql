-- ============================================================================
--  Table: analysis_forecasts.base_rates
--
--  UNCONDITIONAL same-window reference for the forecast analysis — the
--  base rate every bucket stat in analysis_forecasts.forecast_results
--  is meant to be read against (lift):
--
--    per (sec_type, code, stat_date, period), over the code's OWN
--    trailing 10-year window (stat_date - 10y, stat_date] — the same
--    window, price space and forward-change definition as the buckets,
--    but over ALL of the code's trading days in the window (not just
--    the extreme days):
--
--      base_ave_change — mean n-day forward fractional change over all
--                        window days (bucket ave_change − base = edge)
--      base_count      — window days with a valid n-day forward change
--                        (denominator)
--
--  (The reversal base probabilities base_down_prob / base_up_prob +
--  their threshold bar were REMOVED 2026-09-25 with
--  forecast_results.reverse_prob — see
--  analyze/analysis_forecasts/config/horizons.py.)
--
--  Full-window gate: identical to the mov_* tables (a code enters a
--  stat_date only once its own history strictly precedes the window
--  start). One row per (code, period); rows are emitted only where
--  base_count > 0. Populated by python -m analyze.analysis_forecasts
--  alongside the mov_* tables.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.base_rates (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    stat_date      DATE         NOT NULL,  -- annual snapshot date (completed year-end); window (stat_date - 10y, stat_date]
    period          TEXT         NOT NULL,  -- 'next' | '5d' | '20d' | 'mixed'

    -- Trailing calendar window the rates were computed over (recorded
    -- build parameter): '10y' = (stat_date - 10y, stat_date]
    lookback_period TEXT         NOT NULL DEFAULT '10y',

    base_count      BIGINT,                 -- window days with a valid n-day forward change (denominator)
    base_ave_change NUMERIC(10,6),          -- mean n-day forward fractional change over ALL window days

    CONSTRAINT pk_base_rates PRIMARY KEY (sec_type, code, stat_date, period)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — same convention as the
-- mov_* motivation tables
-- (database/sql/00_partition_utils.sql; children _p00.._p15).
SELECT public.create_hash_partitions('analysis_forecasts', 'base_rates', 16);

-- 2026-09-25 reversal-probability removal: the base probs + their bar
-- go with forecast_results.reverse_prob (see
-- analyze/analysis_forecasts/config/horizons.py) — dropped here so
-- existing installations lose the columns; fresh installs never
-- create them.
ALTER TABLE analysis_forecasts.base_rates
    DROP COLUMN IF EXISTS base_down_prob;
ALTER TABLE analysis_forecasts.base_rates
    DROP COLUMN IF EXISTS base_up_prob;
ALTER TABLE analysis_forecasts.base_rates
    DROP COLUMN IF EXISTS threshold;

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.base_rates IS 'Unconditional same-window base rates for the forecast analysis: per (sec_type, code, stat_date, period) the mean n-day forward fractional change over ALL of the code''s trading days in the trailing 10-year window ending at stat_date (not just the extreme bucket days), plus the valid-day count. Reference for reading forecast_results.ave_change as lift. Same window / price space / full-window gate as the mov_* tables. Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.base_rates.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_forecasts.base_rates.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_forecasts.base_rates.stat_date IS 'Snapshot DATE on the annual grid (completed year-end). The rates are computed over the trailing 10-year window (stat_date - 10 years, stat_date] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.base_rates.period IS 'Forward horizon period: ''next'' (next-day), ''5d'' (5 trading days), ''20d'' (20 trading days), or ''mixed'' — the FIXED-weight blend of the three horizon rows (5d 0.65 / next 0.25 / 20d 0.10; weights renormalized over the horizons with valid data, base_count = the MIN valid count over those legs) — the unconditional reference of the bucket rows'' blended period=''mixed'' profile. PK member.';

-- ----------------------------------------------------------------------------
--  Migration (2026-09-20 horizon-60d removal + mixed rebalance): the
--  retired '60d' rows are purged, and every (sec_type, code, stat_date)
--  has its weight-blended 'mixed' base-rate row REGENERATED — the
--  FIXED-weight blend of its (now three) horizon rows at 5d 0.65 /
--  next 0.25 / 20d 0.10 (the MIXED_HORIZON_WEIGHTS of
--  analyze.analysis_forecasts.config) — the unconditional reference the
--  bucket rows' blended 'mixed' period is read against (lift stays in
--  one scale; the analysis_signals gate consumes both). Existing mixed
--  rows are deleted first — they were blended under the retired
--  5d 0.50 / next 0.30 / 20d 0.15 / 60d 0.05 weights and over a 60d
--  leg. Idempotent: re-applies re-blend deterministically from the
--  horizon legs; the forecasts writer emits the row natively from its
--  next run (or --force rebuild).
--
--  Blend semantics (mirroring compute_base.compute_base_rate_rows):
--    base_ave_change — the weight mean of the horizon values, weights
--        renormalized over the horizons with a positive base_count;
--    base_count — MIN over those legs (the blend is only as
--        well-observed as its weakest leg), 0 when none.
-- ----------------------------------------------------------------------------
DELETE FROM analysis_forecasts.base_rates WHERE period = '60d';
DELETE FROM analysis_forecasts.base_rates WHERE period = 'mixed';

WITH w(period, wt) AS (
    VALUES ('next', 0.25::float8), ('5d', 0.65::float8),
           ('20d', 0.10::float8)
), leg AS (
    SELECT b.sec_type, b.code, b.stat_date,
           w.wt,
           b.lookback_period,
           COALESCE(b.base_count, 0) AS cnt,
           b.base_ave_change::float8 AS ave
    FROM analysis_forecasts.base_rates b
    JOIN w ON w.period = b.period
    WHERE b.period <> 'mixed'
), agg AS (
    SELECT l.sec_type, l.code, l.stat_date,
           (ARRAY_AGG(l.lookback_period))[1] AS lookback_period,
           SUM(l.wt) FILTER (WHERE l.ave IS NOT NULL) AS w_stat,
           SUM(l.wt * l.ave) AS s_ave,
           MIN(l.cnt) FILTER (WHERE l.cnt > 0) AS cnt_min
    FROM leg l
    GROUP BY l.sec_type, l.code, l.stat_date
)
INSERT INTO analysis_forecasts.base_rates
       (sec_type, code, stat_date, period, lookback_period,
        base_count, base_ave_change)
SELECT a.sec_type, a.code, a.stat_date, 'mixed', a.lookback_period,
       COALESCE(a.cnt_min, 0),
       a.s_ave / a.w_stat
FROM agg a
WHERE NOT EXISTS (
    SELECT 1 FROM analysis_forecasts.base_rates m
    WHERE m.sec_type = a.sec_type AND m.code = a.code
      AND m.stat_date = a.stat_date AND m.period = 'mixed'
);
COMMENT ON COLUMN analysis_forecasts.base_rates.base_count IS 'Number of the code''s window days with a valid n-trading-day forward change — the denominator of base_ave_change.';
COMMENT ON COLUMN analysis_forecasts.base_rates.base_ave_change IS 'Mean n-trading-day forward fractional change over ALL window days with a valid n-day forward change. Baseline for forecast_results.ave_change (bucket mean − base = conditional edge).';
COMMENT ON COLUMN analysis_forecasts.base_rates.lookback_period IS 'Recorded build parameter: the trailing calendar window the rates were computed over — ''10y'' = (stat_date - 10 years, stat_date]. Default ''10y''; a rebuild with a different lookback requires --force.';
