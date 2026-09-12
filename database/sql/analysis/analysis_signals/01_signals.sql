-- ============================================================================
--  Table: analysis_signals.signals
--
--  One row per emitted signal day: (code, sec_type, signal_type,
--  signal_sub_type, date) — the PK. Each row records the day's
--  threshold value, a human-readable reason, the full detection
--  parameter set (JSON), the action, and the forecast confidence.
--
--  signal_type / signal_sub_type combos (current build):
--    mov_rsi + rsi{W}   — rsi_{W}days in the top 1% (side=top →
--                         action=sell) or bottom 1% (side=bottom →
--                         action=buy) of the trailing 5-year window
--                         ending at the snapshot month; W ∈
--                         {6, 10, 14, 20, 60} (mirrors
--                         analysis.mov_ave_rsi), cooldown 5.
--    mov_std + std{W}   — price beyond the 2σ Bollinger band:
--                         upper (price > ma_{W} + 2.0·std_{W}days →
--                         action=sell) or lower (price < ma_{W} −
--                         2.0·std_{W}days → action=buy); W ∈
--                         {5, 20, 60}, cooldown 5.
--    mov_gap + gap{W}   — gap_{W}days (the W-day price return, from
--                         analysis.mov_ave_rsi) in the top 1% (side=top
--                         — sharp W-day rally → action=sell) or bottom
--                         1% (side=bottom — sharp W-day selloff →
--                         action=buy) of the trailing 5-year window;
--                         W ∈ {2, 3}, cooldown 5.
--    mov_pairs + pair{W}   — the CROSS days of the EXISTING relative
--                            MA spread ma5_vs_ma{W} (the parent
--                            mov_ave_spread analysis's own spread
--                            definition, no new MA computation):
--                            side=top a CROSS UP / golden cross
--                            (spread turns > 0 from <= 0 →
--                            action=sell), side=bottom a CROSS DOWN /
--                            death cross (turns < 0 from >= 0 →
--                            action=buy); W ∈ {60, 120, 255}, cooldown
--                            5; signal_threshold = 0 (the crossed
--                            level is the ZERO line).
--    mov_pairs_ema + emapair{W} — the EMA sibling on the EXISTING
--                            ema6_vs_ema{W} spreads (same sides /
--                            cooldown).
--    high_low_streaks + p{band_period}_{pct_type} — the MEAN-MID
--                         anchor day (the ((day_count-1)//2 + 1)-th
--                         trading day of the span; an 8-day streak
--                         anchors its 4th day) of every band-break
--                         excursion streak of
--                         analysis.mov_ave_high_low_pct_streaks, per
--                         band combo (band_period 255/500/750/1275 ×
--                         pct_type 1/5/10 — the analysis_forecasts.
--                         high_low_streaks buckets' trigger days 1:1):
--                         side=top an ABOVE-band excursion (close on
--                         end_date above the end month's high_val band
--                         → action=sell), side=bottom BELOW-band (→
--                         action=buy — mean reversion: below-band
--                         streaks drift UP from the mid anchor, above-
--                         band DOWN). EX-POST anchor: a month is
--                         computed only once every streak anchored in
--                         it is final (2-completed-month resolve lag +
--                         a closed-streak guard). No cooldown.
--
--  signal_threshold — the detection threshold that the day crossed:
--    mov_rsi: the window's linear-interpolated percentile of
--             rsi_{W}days (top 1% or bottom 1% quantile, 0–100 RSI
--             scale) — constant within (code, month, sub_type);
--    mov_std: the day's band level ma_{W} ± 2.0·std_{W}days (varies
--             daily with ma/std).
--
--  confidence — the DRIVING-FACTOR COMPOSITE of the matching forecast
--  bucket, at the qualifying forecast_results period with the best
--  composite (the argmax period, recorded in params JSON as
--  conf_period — the horizon the confidence speaks about):
--
--    confidence = 0.30·f_t + 0.30·f_sharpe + 0.25·f_lift_prob
--               + 0.15·f_prior
--
--    f_t       = t / (t + 3)                t = dir_ave·√occ / std_change
--    f_sharpe  = 1 - exp(-sharpe / 1.2)     sharpe = dir_ave / std_change
--    f_lift_p  = 1 - exp(-lift_prob / 0.5)  lift_prob = rp - base_prob
--    f_prior   = the code's prior mean base composite (same
--                code/side/period, months < M, windows pooled;
--                neutral 0.20 below 10 prior bucket-periods)
--
--  every factor computed in the SIGNAL'S direction (dir_ave is
--  sign-flipped for top/upper — a buy row's confidence speaks about
--  the upward reversal, a sell row's about the downward), floored at
--  0, and horizon-free, so values compare across periods / families /
--  sec_types. This replaces the former MAX(reverse_prob): rp
--  saturates at long horizons (gate-passers' 20d/60d rp sits at
--  0.6-1.0 quantized by 5-7 occurrences) so the old confidence mostly
--  ranked buckets by horizon. The full factor breakdown rides in the
--  params JSON under confidence_factors.
--
--  Forecast-confirmation gate (forecast-result rule):
--  a day is RECORDED only when the matching analysis_forecasts bucket
--  (same code/sec_type/stat_month/window/side/pct|k/cooldown) has in
--  ANY forecast period (next / 5d / 20d / 60d) reverse_prob > 1%
--  (a material reversal probability) AND that period's mean forward
--  change is a REVERSAL (dir_ave > 0 — the bucket's average outcome
--  reverses, so the signal holds, not just a fat reversal tail) AND
--  that period's reverse_prob beats the unconditional base rate
--  (probability lift) AND that period's mean forward change beats the
--  base drift (magnitude lift — dropped buckets realize ~half the
--  mean reversal OOS). Each row also carries the per-security
--  calibration: tier ('proven' / 'proven_dir' / 'standard'),
--  code_baseline (the code's prior mean composite for the confidence's
--  argmax period) and code_rank (within-code percentile floor of the
--  confidence).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_signals.signals (
    code            TEXT         NOT NULL,  -- ticker (etf "510050.SS" / index "000300" / stock)
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT         NOT NULL,  -- 'mov_rsi' | 'mov_std' — the detection family
    signal_sub_type TEXT         NOT NULL,  -- indicator + window: 'rsi6'..'rsi60' / 'std5'..'std60'
    date            DATE         NOT NULL,  -- the signal day (only dates inside a snapshot month M of analysis_forecasts)

    action          TEXT         NOT NULL,  -- 'sell' (top RSI / upper band) | 'buy' (bottom RSI / lower band)
    signal_threshold NUMERIC(14,6),         -- threshold the day crossed (RSI percentile / band level)
    confidence      NUMERIC(8,6),           -- driving-factor composite at the best qualifying forecast period (evidence t + efficiency sharpe + consistency lift + code prior, action-direction)
    tier            TEXT,                   -- per-security tier: 'proven' | 'proven_dir' | 'standard'
    code_baseline   NUMERIC(8,6),           -- the code's prior mean reverse_prob (confidence's argmax period)
    code_rank       NUMERIC(8,6),           -- within-code percentile floor of the confidence (code's own prior buckets)
    reason          TEXT,                   -- human-readable explanation of the signal
    params          JSONB,                  -- full detection params, e.g. {"rsi_window":14,"side":"top","pct":1,"cooldown_days":5}
    is_active       BOOLEAN      NOT NULL DEFAULT FALSE,  -- TRUE only on the sec_type's LATEST signal date (refreshed after every run)

    CONSTRAINT pk_signals PRIMARY KEY (code, sec_type, signal_type, signal_sub_type, date)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'signals', 16);

-- Date-first lookup (UI / "signals on day X for sec_type Y").
CREATE INDEX IF NOT EXISTS idx_signals_date
    ON analysis_signals.signals (sec_type, signal_type, date);

-- ----------------------------------------------------------------------------
--  Idempotent migrations (pre-existing installs) — MUST precede the
--  Comments section below (comments reference the post-migration names):
--  1. is_active (installs created before is_active existed); ADD COLUMN
--     propagates to all hash partitions.
--  2. price_threshold -> signal_threshold rename.
--  3. confidence (MAX reverse_prob across forecast periods 5d + 20d).
-- ----------------------------------------------------------------------------
ALTER TABLE analysis_signals.signals
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE analysis_signals.signals
    ADD COLUMN IF NOT EXISTS confidence NUMERIC(8,6);

-- Per-security calibration columns (gate study 2026-09: prior-vs-future
-- mean rp correlation 0.80-0.97). ADD COLUMN propagates to all hash
-- partitions; pre-existing rows keep NULL until a --force rebuild.
ALTER TABLE analysis_signals.signals
    ADD COLUMN IF NOT EXISTS tier TEXT;

ALTER TABLE analysis_signals.signals
    ADD COLUMN IF NOT EXISTS code_baseline NUMERIC(8,6);

ALTER TABLE analysis_signals.signals
    ADD COLUMN IF NOT EXISTS code_rank NUMERIC(8,6);

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'analysis_signals'
          AND table_name   = 'signals'
          AND column_name  = 'price_threshold'
    ) THEN
        ALTER TABLE analysis_signals.signals
            RENAME COLUMN price_threshold TO signal_threshold;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_signals.signals IS 'Per-day buy/sell signals mirroring the analysis_forecasts extreme-day detection (mov_rsi top/bottom-1% RSI days; mov_std 2σ Bollinger breaches; mov_gap top/bottom-1% N-day price-return days; px_vol price-speed × volume states; margin_ratio margin-buy intensity states; high_low_streaks the MEAN-MID anchor day of every band-break excursion streak — the analysis_forecasts.high_low_streaks buckets'' trigger days 1:1, EX-POST so target months carry a 2-month resolve lag + a closed-streak guard) with the same trailing 5-year window, percentile/band/state thresholds, cooldown suppression (mov_* families) and full-window history gate. A day is recorded ONLY when the matching forecast bucket clears the forecast-confirmation gate (forecast-result rule): in at least one forecast_results period (next/5d/20d/60d) the bucket''s reverse_prob > 1% (a material reversal probability) AND that period''s mean forward change is a REVERSAL (dir_ave > 0 — the bucket''s average outcome reverses, so the signal holds, not just a fat reversal tail) AND that period''s reverse_prob beats the unconditional base rate (probability lift) AND that period''s mean forward change beats the base drift (magnitude lift). Each row carries the driving-factor composite confidence (evidence t-stat + efficiency sharpe + consistency probability lift + per-code prior calibration, computed in the signal''s direction) plus the per-security calibration (tier / code_baseline / code_rank). One row per (code, sec_type, signal_type, signal_sub_type, date); a date is emitted only within its own snapshot month M (the month must already exist in analysis_forecasts for the matching config). Populated incrementally by python -m analyze.analysis_signals; --force deletes the sec_type''s rows and recomputes.';
COMMENT ON COLUMN analysis_signals.signals.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_signals.signals.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_signals.signals.signal_type IS 'Detection family: mov_rsi (RSI extreme-percentile day), mov_std (Bollinger band breach day), mov_gap (N-day price-return extreme day), px_vol (price-speed × amount-level state day), margin_ratio (margin-buy intensity state day) or high_low_streaks (the MEAN-MID anchor day of a band-break excursion streak) — mirrors the matching analysis_forecasts bucket table.';
COMMENT ON COLUMN analysis_signals.signals.signal_sub_type IS 'Indicator + window: rsi{W} for mov_rsi (W = RSI window 6/10/14/20/60), std{W} for mov_std (W = MA/σ window 5/20/60 — band ma_{W} ± 2.0·std_{W}days), gap{W} for mov_gap (W = gap window 2/3 — gap_{W}days N-day return), p{band_period}_{pct_type} for high_low_streaks (the audited band: 255/500/750/1275 trading rows × 1/5/10 percent).';
COMMENT ON COLUMN analysis_signals.signals.date IS 'The signal day. Only dates inside a snapshot month M whose analysis_forecasts snapshot already exists; the detection window is the trailing 5 years (M - 5y, M].';
COMMENT ON COLUMN analysis_signals.signals.action IS 'Trading action implied by the side: sell for mov_rsi top (overbought) / mov_std upper breach / mov_gap top (sharp rally), buy for mov_rsi bottom (oversold) / mov_std lower breach / mov_gap bottom (sharp selloff).';
COMMENT ON COLUMN analysis_signals.signals.signal_threshold IS 'The detection threshold the day crossed: for mov_rsi the window''s linear-interpolated top/bottom-1% quantile of rsi_{W}days (0–100 scale, constant per code/month/sub_type); for mov_std the day''s band level ma_{W} ± 2.0·std_{W}days (price space, varies daily); for mov_gap the window''s linear-interpolated top/bottom-1% quantile of gap_{W}days (fractional return, constant per code/month/sub_type); for high_low_streaks the streak side''s band edge in PRICE space (high_val for side=top / low_val for bottom — the level the close crossed to be out-of-band).';
COMMENT ON COLUMN analysis_signals.signals.confidence IS 'Driving-factor composite of the matching forecast bucket at its best qualifying period (params JSON conf_period): 0.30·f_t + 0.30·f_sharpe + 0.25·f_lift_prob + 0.15·f_prior, with f_t = t/(t+3), t = dir_ave·√occ/std_change; f_sharpe = 1-exp(-sharpe/1.2), sharpe = dir_ave/std_change; f_lift_prob = 1-exp(-lift_prob/0.5), lift_prob = reverse_prob − base_prob; f_prior = the code''s prior mean base composite (neutral 0.20 below 10 prior bucket-periods). Every factor is computed in the signal''s action direction (buy = upward reversal, sell = downward), floored at 0 and horizon-free, so confidences compare across periods, families and sec_types. Replaces the former MAX(reverse_prob), which saturated at long horizons (0.6-1.0 for 20d/60d). The full breakdown rides in params JSON under confidence_factors. NULL when the forecast bucket has no results.';
COMMENT ON COLUMN analysis_signals.signals.tier IS 'Per-security tier from the confirmation gate (MAX over the bucket''s qualifying periods; code stats need >= 100 prior bucket-periods): ''proven'' — the code''s prior mean composite >= 0.40 (top ~10% of codes by prior bucket quality); ''proven_dir'' — the code''s prior mean DIRECTIONAL move >= 1%; ''standard'' otherwise.';
COMMENT ON COLUMN analysis_signals.signals.code_baseline IS 'The code''s prior mean composite for the confidence''s argmax period (rolling M-1 population of the code''s own buckets, same family/side/period; windows pooled). NULL when the code''s prior history is too short. Validated predictive: prior-vs-future mean rp correlation 0.80-0.97.';
COMMENT ON COLUMN analysis_signals.signals.code_rank IS 'Coarse within-code percentile FLOOR of the confidence: the highest of the code''s own prior P25/P50/P75/P90/P95 composite levels that the confidence clears. NULL below 30 prior bucket-periods.';
COMMENT ON COLUMN analysis_signals.signals.reason IS 'Human-readable explanation: the day''s indicator value vs the threshold (e.g. "rsi14=88.3 >= top 1% threshold 86.9 of trailing 5y window ending 2026-07-31").';
COMMENT ON COLUMN analysis_signals.signals.params IS 'Full detection parameters as JSON: mov_rsi {"rsi_window", "side", "pct", "cooldown_days"}; mov_std {"ma_window", "k", "side", "cooldown_days"}; mov_gap {"gap_window", "side", "pct", "cooldown_days"}; high_low_streaks {"band_period", "pct_type", "side", "day_count", "anchor_pos", "start_date", "end_date", "anchor_close", "band_val"}. Values mirror the analysis_forecasts bucket keys of the matching config.';

COMMENT ON COLUMN analysis_signals.signals.is_active IS 'TRUE only for rows on the sec_type''s LATEST signal date (max(date) per sec_type — the latest date the run wrote); FALSE everywhere else. Refreshed by python -m analyze.analysis_signals after EVERY run (including --force), so exactly one date per sec_type is active at a time. Consumers (e.g. live breach monitoring) use the active rows as the current threshold set.';
