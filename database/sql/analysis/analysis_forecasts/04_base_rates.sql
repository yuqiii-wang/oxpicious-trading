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

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.base_rates IS 'Unconditional same-window base rates for the forecast analysis: per (sec_type, code, stat_date, period) the mean n-day forward fractional change over ALL of the code''s trading days in the trailing 10-year window ending at stat_date (not just the extreme bucket days), plus the valid-day count. Reference for reading forecast_results.ave_change as lift. Same window / price space / full-window gate as the mov_* tables. Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.base_rates.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_forecasts.base_rates.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis_forecasts.base_rates.stat_date IS 'Snapshot DATE on the annual grid (completed year-end). The rates are computed over the trailing 10-year window (stat_date - 10 years, stat_date] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.base_rates.period IS 'Forward horizon period: ''next'' (next-day), ''5d'' (5 trading days), ''20d'' (20 trading days), or ''mixed'' — the FIXED-weight blend of the three horizon rows (5d 0.65 / next 0.25 / 20d 0.10; weights renormalized over the horizons with valid data, base_count = the MIN valid count over those legs) — the unconditional reference of the bucket rows'' blended period=''mixed'' profile. PK member.';
COMMENT ON COLUMN analysis_forecasts.base_rates.base_count IS 'Number of the code''s window days with a valid n-trading-day forward change — the denominator of base_ave_change.';
COMMENT ON COLUMN analysis_forecasts.base_ave_change IS 'Mean n-trading-day forward fractional change over ALL window days with a valid n-day forward change. Baseline for forecast_results.ave_change (bucket mean − base = conditional edge).';
COMMENT ON COLUMN analysis_forecasts.base_rates.lookback_period IS 'Recorded build parameter: the trailing calendar window the rates were computed over — ''10y'' = (stat_date - 10 years, stat_date]. Default ''10y''; a rebuild with a different lookback requires --force.';
