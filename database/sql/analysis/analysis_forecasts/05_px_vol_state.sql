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
--    vol_state — the day's log trading-amount LEVEL z-scored by the
--               code's own trailing distribution (the LEVEL statement
--               heavy/shrink claim):
--                 z = (log(trading_amount[t]) - μ) / σ   with μ/σ =
--                 rolling 255-row, min_periods 60, shifted 1 row
--                 heavy  : z >  z_heavy   (default 2.0)
--                 normal : z_shrink <= z <= z_heavy  (-0.92 / 2.0)
--                 shrink : z <  z_shrink  (default -0.92)
--               NULL trading_amount (or missing base window) → no
--               bucket that day.
--
--  Buckets are STREAK-MERGED like the mov_* extreme-EVENT buckets
--  (2026-09): consecutive days holding the same (speed, vol) state
--  collapse into ONE forecast signal anchored at the run's MID day —
--  the mean run length lives on forecast_identities.streak_signal_days
--  and each result row's streak_starts / streak_ends / streak_days
--  carry the merged runs — split by is_market_hyped (ANY bucket date
--  inside a mov_ave_market_hypes episode) exactly like
--  mov_rsi / mov_std. Results live in
--  analysis_forecasts.forecast_results via forecast_id (1:N — one
--  forecast_id → 5 period rows next/5d/20d/60d/mixed: ave/std/max/min
--  forward change, occurrence_count and
--  reverse_prob at the bucket's ADAPTIVE threshold
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
--  CODE-CLUSTERED, forecast_id-keyed (2026-09): PK (code, forecast_id)
--  on HASH (code) partitions — the code-clustered read/write axis (a
--  per-security read prunes to ONE partition and walks the code-leading
--  PK; forecast_id-only joins/searches use the secondary
--  idx_px_vol_state_forecast_id). code is the ONLY identity column stored
--  here (the partition key); sec_type and stat_month live ONLY in
--  analysis_forecasts.forecast_identities — the search table
--  (see 02_mov_rsi_mov_std.sql).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.px_vol_state (
    code            TEXT         NOT NULL,  -- hash partition key + PK lead; sec_type / stat_month live in forecast_identities
    forecast_id     BIGINT      NOT NULL,  -- 1:N link to the bucket's 5 forecast_results period rows; id-only joins/searches use idx_px_vol_state_forecast_id
    px_speed        TEXT         NOT NULL,  -- 'sharp_up' | 'slow_up' | 'flat' | 'slow_dn' | 'sharp_dn' (t = ret/σ_ret bars)
    vol_state       TEXT         NOT NULL,  -- 'heavy' | 'normal' | 'shrink' (amount-level z bars)
    side            TEXT         NOT NULL,  -- 'top' (up speeds) | 'bottom' (down speeds) | 'flat' — reversal direction of reverse_prob

    -- Recorded build parameters (NOT PK — recorded for provenance; a
    -- rebuild with different values requires --force).
    sigma_window    INTEGER      NOT NULL DEFAULT 255,   -- rolling σ_ret / log-amount level moments window (rows)
    lb_window       INTEGER      NOT NULL DEFAULT 5,     -- classic 量比 base window (evidence-only since log_level)
    k_slow_up       NUMERIC(4,2) NOT NULL DEFAULT 1.26,  -- slow_up lower t-bar (calibrated to the legacy ±2% trigger rate)
    k_slow_dn       NUMERIC(4,2) NOT NULL DEFAULT 1.29,  -- slow_dn upper |t|-bar
    k_sharp         NUMERIC(4,2) NOT NULL DEFAULT 2.00,  -- sharp vs slow t-bar
    z_heavy         NUMERIC(4,2) NOT NULL DEFAULT 2.00,  -- heavy (Amt Up) z-bar
    z_shrink        NUMERIC(4,2) NOT NULL DEFAULT -0.92, -- shrink (Amt Down) z-bar
    sigma_floor     NUMERIC(6,4) NOT NULL DEFAULT 0.005, -- σ_ret floor: below this (bond-like) no bucket fires
    lookback_period TEXT         NOT NULL DEFAULT '5y', -- trailing window the bucket was computed over ('5y' = stat_month - 5y .. stat_month)

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_px_vol_state PRIMARY KEY (code, forecast_id)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'px_vol_state', 16);

CREATE INDEX IF NOT EXISTS idx_px_vol_state_forecast_id
    ON analysis_forecasts.px_vol_state (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.px_vol_state IS 'Recent-day price-change × trading-amount state buckets (motivation): one row per forecast_id — the window days of one security-month whose σ-standardized 1-day price change (t = ret_1d / rolling-255 σ_ret of the code, shifted 1 row) and z-scored log trading-amount LEVEL (z vs rolling-255 moments of log(trading_amount), shifted 1 row — a LEVEL statement vs the code''s own trailing-year amount distribution) simultaneously fall in the named states, with a σ_ret floor of 0.005 excluding bond-like indices. Adaptive per-code thresholds recorded in the row (k_slow_up 1.26 / k_slow_dn 1.29 / k_sharp 2.0 / z_heavy 2.0 / z_shrink -0.92 — calibrated to the legacy ±2% / 量比 1.5 / 0.8 trigger rates). State runs streak-merge into ONE mid-anchored forecast signal (2026-09 — consecutive same-state days collapse to the run''s MID day; mean run length on forecast_identities.streak_signal_days). Keyed by the surrogate forecast_id (hash partition key); the shared identity (sec_type, code, stat_month) + bucket family live in analysis_forecasts.forecast_identities. Results (forward changes / FIXED 1% threshold — period-end n-day close vs ±1% — reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id; flat rows carry side=''flat'' and NULL reverse_prob. Sources: stats.*_basic_stats (close, trading_amount), stats.*_liquidity_margin (etf/stock trading_amount). Populated by python -m analyze.analysis_forecasts.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.forecast_id IS 'Surrogate PK + hash-partition key (1:N link to the bucket''s 5 period rows in analysis_forecasts.forecast_results, allocated by the writer, shared across all 5 periods). The bucket''s identity (sec_type, code, stat_month) + bucket family are registered in analysis_forecasts.forecast_identities under this id.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.px_speed IS 'Price-speed state of the day: t = ret_1d / σ_ret(code, 255 rows ending t-1, min 60). sharp_up t > 2.0; slow_up 1.26 < t <= 2.0; flat -1.29 <= t <= 1.26; slow_dn -2.0 <= t < -1.29; sharp_dn t < -2.0. Never fires when σ_ret is NaN or below sigma_floor (0.005).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.vol_state IS 'Trading-amount state of the day: z = (log(trading_amount[t]) - μ) / σ where μ/σ are the code''s rolling-255 (min 60) moments of log(trading_amount), shifted 1 row — a LEVEL statement vs the code''s own trailing-year amount distribution. heavy z > 2.0; normal -0.92 <= z <= 2.0; shrink z < -0.92. NULL trading_amount → no bucket.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.side IS 'Reversal side of the bucket''s forecast_results.reverse_prob: top (sharp_up/slow_up — reversal = n-day change below -threshold), bottom (slow_dn/sharp_dn — reversal above +threshold), flat (no directional claim; reverse_prob NULL). Mirrors the mov_* side semantics so analysis_signals.gate can consume the table unchanged.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.sigma_window IS 'Recorded build parameter: rolling window (rows, min_periods 60) of σ_ret and of the log-amount level moments μ/σ. All shifted 1 row before use (no look-ahead). Default 255.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.lb_window IS 'Recorded build parameter: classic 量比 base window — evidence-only since the log_level refactor. Default 5.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.k_slow_up IS 'Recorded build parameter: slow_up lower t-bar (default 1.26 — the ±1.26σ band reproduces the legacy fixed ±2% up trigger rate pooled across equity-like indices).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.k_slow_dn IS 'Recorded build parameter: slow_dn upper |t|-bar (default 1.29 — the legacy fixed -2% down trigger rate).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.k_sharp IS 'Recorded build parameter: sharp vs slow t-bar (default 2.0 — ≥2σ days are "sharp"; the 2026-09 speed study shows continuation/reversal edges concentrate at t beyond ±2σ).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.z_heavy IS 'Recorded build parameter: heavy (Amt Up) z-bar (default 2.0).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.z_shrink IS 'Recorded build parameter: shrink (Amt Down) z-bar (default -0.92).';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.sigma_floor IS 'Recorded build parameter: minimum σ_ret for a day to join any bucket (default 0.005). Bond-like indices (σ_ret ≈ 0.01–0.02%) would classify tiny wiggles as extremes — they are excluded by the floor.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.lookback_period IS 'Recorded build parameter (NOT a PK member): the trailing calendar window the bucket was computed over — ''5y'' = (stat_month - 5 years, stat_month]. Default ''5y''; a rebuild with a different lookback requires --force.';
COMMENT ON COLUMN analysis_forecasts.px_vol_state.is_market_hyped IS 'TRUE when ANY of the bucket''s dates falls inside one of the code''s stats.mov_ave_market_hypes episodes (any min_checkin_period).';

-- ----------------------------------------------------------------------------
--  Data-quality gate: the px_vol_state vocabularies (shared helpers, see
--  01_forecast_results.sql / 00_partition_utils.sql). NOT VALID first,
--  validated once by the schema-wide sweep below.
-- ----------------------------------------------------------------------------
SELECT public.ensure_check_constraint(
    'analysis_forecasts.px_vol_state',
    'chk_px_vol_state_side',
    $chk$side IN ('top', 'bottom', 'flat')$chk$);
SELECT public.ensure_check_constraint(
    'analysis_forecasts.px_vol_state',
    'chk_px_vol_state_px_speed',
    $chk$px_speed IN ('sharp_up', 'slow_up', 'flat', 'slow_dn', 'sharp_dn')$chk$);
SELECT public.ensure_check_constraint(
    'analysis_forecasts.px_vol_state',
    'chk_px_vol_state_vol_state',
    $chk$vol_state IN ('heavy', 'normal', 'shrink')$chk$);
SELECT public.validate_pending_checks('analysis_forecasts');
