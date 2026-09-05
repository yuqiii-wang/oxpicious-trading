-- ============================================================================
--  Table: analysis_forecasts.px_vol_state
--
--  Fourth MOTIVATION (bucket-defining) table of the forecast analysis:
--  recent-day price-change × trading-amount state buckets, with
--  per-index ADAPTIVE thresholds derived from each code's own rolling
--  std (2026-09 studies: temp_scripts/study_ma_spread_index_9grid*.py,
--  study_ma_spread_index_sharp_slow.py, study_px_vol_state_forecast.py).
--  NO rsi / ma / std band inputs — purely price changes + trading amt.
--
--  A (code, date) joins a bucket when BOTH state legs hold, evaluated
--  with information available at that day (every rolling stat is
--  shifted 1 row → no look-ahead):
--
--    px_speed — the day's 1-row fractional price change ret_1d,
--               standardized by the code's OWN trailing σ:
--                 t = ret_1d / σ_ret(code, t-255..t-1)
--               (σ_ret = rolling 255-row sample std of ret_1d,
--               min_periods 60, shifted 1 row). Undefined (never a
--               bucket) when σ_ret is NaN or below sigma_floor
--               (0.005 — bond-like indices are excluded, their tiny
--               σ makes any wiggle a false extreme):
--                 sharp_up : t >  k_sharp   (default 2.0)
--                 slow_up  : k_slow_up < t <= k_sharp  (1.26 / 2.0)
--                 flat     : -k_slow_dn <= t <= k_slow_up  (-1.29 / 1.26)
--                 slow_dn  : -k_sharp <= t < -k_slow_dn (-2.0 / -1.29)
--                 sharp_dn : t < -k_sharp
--
--    vol_state — the day's 量比 (liangbi) z-scored by the code's own
--               trailing moments:
--                 liangbi = trading_amount[t] / mean(trading_amount,
--                           t-5..t-1)   (classic, excludes today)
--                 z = (liangbi - μ_lb) / σ_lb   with μ/σ = rolling
--                 255-row, min_periods 60, shifted 1 row
--                 heavy  : z >  z_heavy   (default 2.0)
--                 normal : z_shrink <= z <= z_heavy  (-0.92 / 2.0)
--                 shrink : z <  z_shrink  (default -0.92)
--               NULL trading_amount (or missing base window) → no
--               bucket that day.
--
--  Buckets are STATE cells (every qualifying day joins — no cooldown,
--  unlike the mov_* extreme-EVENT buckets), split by PK member
--  is_market_hyped (ANY bucket date inside a mov_ave_market_hypes
--  episode) exactly like mov_rsi / mov_std / mov_gap. Results live in
--  analysis_forecasts.forecast_results via forecast_id (1:N — one
--  forecast_id → 4 period rows next/5d/20d/60d: ave/std/max/min
--  forward change, occurrence_count, max_low_change_ratio and
--  reverse_prob at the bucket's ADAPTIVE reverse_threshold
--  (k_n·σ of the code's window forward changes)). The reversal side
--  follows the ``side`` column: top (up speeds) reverses on change
--  < -threshold, bottom (down speeds) on change > +threshold; flat
--  rows carry side='flat' and NULL reverse_prob (no directional
--  claim). The signals layer reads the cross-period
--  MAX(reverse_prob) as each signal row's confidence.
--
--  Threshold columns are RECORDED BUILD PARAMETERS (like
--  mov_ave_market_hypes' thresholds): NOT part of the PK — rebuilding
--  with different values (--force) overwrites in place. The full-5y
--  window gate is identical to the other engines (a code enters a
--  stat_month only once its own history strictly precedes the window
--  start).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.px_vol_state (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    stat_month      DATE         NOT NULL,  -- completed month-end; bucket over trailing 5y window (stat_month - 5y, stat_month]
    px_speed        TEXT         NOT NULL,  -- 'sharp_up' | 'slow_up' | 'flat' | 'slow_dn' | 'sharp_dn' (t = ret/σ_ret bars)
    vol_state       TEXT         NOT NULL,  -- 'heavy' | 'normal' | 'shrink' (z_量比 bars)
    side            TEXT         NOT NULL,  -- 'top' (up speeds) | 'bottom' (down speeds) | 'flat' — reversal direction of reverse_prob

    -- Recorded build parameters (NOT PK — recorded for provenance; a
    -- rebuild with different values requires --force).
    sigma_window    INTEGER      NOT NULL DEFAULT 255,   -- rolling σ_ret / μ_lb / σ_lb window (rows)
    lb_window       INTEGER      NOT NULL DEFAULT 5,     -- 量比 base window (trading_amount mean of t-5..t-1)
    k_slow_up       NUMERIC(4,2) NOT NULL DEFAULT 1.26,  -- slow_up lower t-bar (calibrated to the legacy ±2% trigger rate)
    k_slow_dn       NUMERIC(4,2) NOT NULL DEFAULT 1.29,  -- slow_dn upper |t|-bar
    k_sharp         NUMERIC(4,2) NOT NULL DEFAULT 2.00,  -- sharp vs slow t-bar
    z_heavy         NUMERIC(4,2) NOT NULL DEFAULT 2.00,  -- heavy z-bar (calibrated to the legacy 量比>1.5 rate)
    z_shrink        NUMERIC(4,2) NOT NULL DEFAULT -0.92, -- shrink z-bar (calibrated to the legacy 量比<0.8 rate)
    sigma_floor     NUMERIC(6,4) NOT NULL DEFAULT 0.005, -- σ_ret floor: below this (bond-like) no bucket fires

    forecast_id      BIGINT      NOT NULL,  -- 1:N link → forecast_results (4 period rows)

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_px_vol_state PRIMARY KEY (code, sec_type, stat_month, px_speed, vol_state, is_market_hyped)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'px_vol_state', 16);

CREATE INDEX IF NOT EXISTS idx_px_vol_state_forecast_id
    ON analysis_forecasts.px_vol_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.px_vol_state IS 'Recent-day price-change × trading-amount state buckets (motivation): per (code, sec_type, stat_month, px_speed, vol_state, is_market_hyped) — the window days whose σ-standardized 1-day price change (t = ret_1d / rolling-255 σ_ret of the code, shifted 1 row) and z-scored 量比 (trading_amount vs its own 5-row trailing mean, z vs rolling-255 moments, shifted 1 row) simultaneously fall in the named states, with a σ_ret floor of 0.005 excluding bond-like indices. Adaptive per-code thresholds recorded in the row (k_slow_up 1.26 / k_slow_dn 1.29 / k_sharp 2.0 / z_heavy 2.0 / z_shrink -0.92 — calibrated to the legacy ±2% / 量比 1.5 / 0.8 trigger rates). State cells: no cooldown. Results (forward changes / adaptive reverse_threshold k·σ reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id; flat rows carry side=''flat'' and NULL reverse_prob. Sources: stats.*_basic_stats (close, trading_amount), stats.*_liquidity_margin (etf/stock trading_amount). Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.stat_month IS 'Completed month-end date. The bucket is computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.px_speed IS 'Price-speed state of the day: t = ret_1d / σ_ret(code, 255 rows ending t-1, min 60). sharp_up t > 2.0; slow_up 1.26 < t <= 2.0; flat -1.29 <= t <= 1.26; slow_dn -2.0 <= t < -1.29; sharp_dn t < -2.0. Never fires when σ_ret is NaN or below sigma_floor (0.005).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.vol_state IS 'Trading-amount state of the day: z = (liangbi - μ) / σ where liangbi = trading_amount[t] / mean(trading_amount[t-5..t-1]) and μ/σ are the code''s rolling-255 (min 60) moments of liangbi, shifted 1 row. heavy z > 2.0; normal -0.92 <= z <= 2.0; shrink z < -0.92. NULL trading_amount → no bucket.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.side IS 'Reversal side of the bucket''s forecast_results.reverse_prob: top (sharp_up/slow_up — reversal = n-day change below -reverse_threshold), bottom (slow_dn/sharp_dn — reversal above +reverse_threshold), flat (no directional claim; reverse_prob NULL). Mirrors the mov_* side semantics so analysis_signals.gate can consume the table unchanged.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.sigma_window IS 'Recorded build parameter: rolling window (rows, min_periods 60) of σ_ret and of the liangbi moments μ/σ. All shifted 1 row before use (no look-ahead). Default 255.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.lb_window IS 'Recorded build parameter: 量比 base window — liangbi = trading_amount[t] / mean(trading_amount[t-lb_window..t-1]). Default 5.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.k_slow_up IS 'Recorded build parameter: slow_up lower t-bar (default 1.26 — the ±1.26σ band reproduces the legacy fixed ±2% up trigger rate pooled across equity-like indices).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.k_slow_dn IS 'Recorded build parameter: slow_dn upper |t|-bar (default 1.29 — the legacy fixed -2% down trigger rate).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.k_sharp IS 'Recorded build parameter: sharp vs slow t-bar (default 2.0 — ≥2σ days are "sharp"; the 2026-09 speed study shows continuation/reversal edges concentrate at t beyond ±2σ).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.z_heavy IS 'Recorded build parameter: heavy (放量) z-bar (default 2.0 — reproduces the legacy 量比>1.5 trigger rate pooled across equity-like indices).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.z_shrink IS 'Recorded build parameter: shrink (缩量) z-bar (default -0.92 — reproduces the legacy 量比<0.8 trigger rate).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.sigma_floor IS 'Recorded build parameter: minimum σ_ret for a day to join any bucket (default 0.005). Bond-like indices (σ_ret ≈ 0.01–0.02%) would classify tiny wiggles as extremes — they are excluded by the floor.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.forecast_id IS '1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results (indexed; allocated by the writer, shared across all 4 periods).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.is_market_hyped IS 'Part of the PK: TRUE when ANY of the bucket''s dates falls inside one of the code''s analysis.mov_ave_market_hypes episodes (any min_checkin_period).';
