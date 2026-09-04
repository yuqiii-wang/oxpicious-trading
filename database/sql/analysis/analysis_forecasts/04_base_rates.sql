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
--      base_down_prob  — P(n-day change < -1%) — the base rate for
--                        top/upper-side reverse_prob
--      base_up_prob    — P(n-day change > +1%) — the base rate for
--                        bottom/lower-side reverse_prob
--      base_count      — window days with a valid n-day forward change
--                        (denominator of all three)
--
--  A fixed ±1% reversal threshold is nearly saturated at the 20d/60d
--  horizons (close to the unconditional rate) — comparing reverse_prob
--  against base_down_prob / base_up_prob is what makes the per-horizon
--  reversal probabilities interpretable.
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
    period          TEXT         NOT NULL,  -- 'next' | '5d' | '20d' | '60d'

    base_count      BIGINT,                 -- window days with a valid n-day forward change (denominator)
    base_ave_change NUMERIC(10,6),          -- mean n-day forward fractional change over ALL window days
    base_down_prob  NUMERIC(8,6),           -- P(n-day change < -1%) over ALL window days (top/upper-side reverse_prob base)
    base_up_prob    NUMERIC(8,6),           -- P(n-day change > +1%) over ALL window days (bottom/lower-side reverse_prob base)

    CONSTRAINT pk_base_rates PRIMARY KEY (sec_type, code, stat_month, period)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — same convention as the
-- mov_* motivation tables
-- (database/sql/00_partition_utils.sql; children _p00.._p15).
SELECT public.create_hash_partitions('analysis_forecasts', 'base_rates', 16);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.base_rates IS 'Unconditional same-window base rates for the forecast analysis: per (sec_type, code, stat_month, period) the mean n-day forward fractional change, P(change < -1%) and P(change > +1%) over ALL of the code''s trading days in the trailing 5-year window ending at stat_month (not just the extreme bucket days), plus the valid-day count. Reference for reading forecast_results.ave_change / reverse_prob as lift. Same window / price space / full-window gate as the mov_* tables. Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.base_rates.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_forecasts.base_rates.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_forecasts.base_rates.stat_month IS 'Completed month-end date. The rates are computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.base_rates.period IS 'Forward horizon period: ''next'' (next-day), ''5d'' (5 trading days), ''20d'' (20 trading days), ''60d'' (60 trading days). PK member.';
COMMENT ON COLUMN analysis_forecasts.base_rates.base_count IS 'Number of the code''s window days with a valid n-trading-day forward change — the denominator of base_ave_change / base_down_prob / base_up_prob.';
COMMENT ON COLUMN analysis_forecasts.base_rates.base_ave_change IS 'Mean n-trading-day forward fractional change over ALL window days with a valid n-day forward change. Baseline for forecast_results.ave_change (bucket mean − base = conditional edge).';
COMMENT ON COLUMN analysis_forecasts.base_rates.base_down_prob IS 'P(n-day forward change < -1%) over ALL window days with a valid n-day forward change. Baseline for the reverse_prob of top (RSI/gap) and upper (Bollinger) buckets.';
COMMENT ON COLUMN analysis_forecasts.base_rates.base_up_prob IS 'P(n-day forward change > +1%) over ALL window days with a valid n-day forward change. Baseline for the reverse_prob of bottom (RSI/gap) and lower (Bollinger) buckets.';
