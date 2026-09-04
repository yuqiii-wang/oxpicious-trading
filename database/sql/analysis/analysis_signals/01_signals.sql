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
--
--  signal_threshold — the detection threshold that the day crossed:
--    mov_rsi: the window's linear-interpolated percentile of
--             rsi_{W}days (top 1% or bottom 1% quantile, 0–100 RSI
--             scale) — constant within (code, month, sub_type);
--    mov_std: the day's band level ma_{W} ± 2.0·std_{W}days (varies
--             daily with ma/std).
--
--  confidence — MAX(reverse_prob) across forecast_results periods
--  5d and 20d for the matching forecast bucket (same
--  code/sec_type/stat_month/window/side/pct|k/cooldown). reverse_prob
--  is P(n-day change is a REVERSAL > 1% against the bucket side).
--
--  Forecast confirmation gate: a day is RECORDED only when the matching
--  analysis_forecasts bucket (same code/sec_type/stat_month/window/side/
--  pct|k/cooldown) has reverse_prob > 0 in period 5d OR period 20d
--  (checked from analysis_forecasts.forecast_results via the bucket's
--  forecast_id at write time). Confidence = MAX(reverse_prob) across
--  5d + 20d periods is stored on each row.
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_signals.signals (
    code            TEXT         NOT NULL,  -- ticker (etf "510050.SS" / index "000300" / stock)
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    signal_type     TEXT         NOT NULL,  -- 'mov_rsi' | 'mov_std' — the detection family
    signal_sub_type TEXT         NOT NULL,  -- indicator + window: 'rsi6'..'rsi60' / 'std5'..'std60'
    date            DATE         NOT NULL,  -- the signal day (only dates inside a snapshot month M of analysis_forecasts)

    action          TEXT         NOT NULL,  -- 'sell' (top RSI / upper band) | 'buy' (bottom RSI / lower band)
    signal_threshold NUMERIC(14,6),         -- threshold the day crossed (RSI percentile / band level)
    confidence      NUMERIC(8,6),           -- MAX(reverse_prob) across forecast periods 5d+20d (P of >1% reversal)
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
COMMENT ON TABLE analysis_signals.signals IS 'Per-day buy/sell signals mirroring the analysis_forecasts extreme-day detection (mov_rsi top/bottom-1% RSI days; mov_std 2σ Bollinger breaches) with the same trailing 5-year window, percentile/band thresholds, cooldown suppression and full-window history gate. A day is recorded ONLY when the matching forecast bucket has reverse_prob > 0 in period 5d OR period 20d (checked from analysis_forecasts.forecast_results at write time). Confidence = MAX(reverse_prob) across forecast periods 5d + 20d is stored on each row. One row per (code, sec_type, signal_type, signal_sub_type, date); a date is emitted only within its own snapshot month M (the month must already exist in analysis_forecasts for the matching config). Populated incrementally by python -m analyze.analysis_signals; --force deletes the sec_type''s rows and recomputes.';
COMMENT ON COLUMN analysis_signals.signals.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_signals.signals.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_signals.signals.signal_type IS 'Detection family: mov_rsi (RSI extreme-percentile day), mov_std (Bollinger band breach day) or mov_gap (N-day price-return extreme day) — mirrors the analysis_forecasts mov_rsi / mov_std / mov_gap bucket tables.';
COMMENT ON COLUMN analysis_signals.signals.signal_sub_type IS 'Indicator + window: rsi{W} for mov_rsi (W = RSI window 6/10/14/20/60), std{W} for mov_std (W = MA/σ window 5/20/60 — band ma_{W} ± 2.0·std_{W}days), gap{W} for mov_gap (W = gap window 2/3 — gap_{W}days N-day return).';
COMMENT ON COLUMN analysis_signals.signals.date IS 'The signal day. Only dates inside a snapshot month M whose analysis_forecasts snapshot already exists; the detection window is the trailing 5 years (M - 5y, M].';
COMMENT ON COLUMN analysis_signals.signals.action IS 'Trading action implied by the side: sell for mov_rsi top (overbought) / mov_std upper breach / mov_gap top (sharp rally), buy for mov_rsi bottom (oversold) / mov_std lower breach / mov_gap bottom (sharp selloff).';
COMMENT ON COLUMN analysis_signals.signals.signal_threshold IS 'The detection threshold the day crossed: for mov_rsi the window''s linear-interpolated top/bottom-1% quantile of rsi_{W}days (0–100 scale, constant per code/month/sub_type); for mov_std the day''s band level ma_{W} ± 2.0·std_{W}days (price space, varies daily); for mov_gap the window''s linear-interpolated top/bottom-1% quantile of gap_{W}days (fractional return, constant per code/month/sub_type).';
COMMENT ON COLUMN analysis_signals.signals.confidence IS 'MAX(reverse_prob) across forecast_results periods 5d and 20d for the matching forecast bucket. reverse_prob = P(n-day forward change is a REVERSAL > 1% against the bucket side). NULL when the forecast bucket has no 5d/20d results.';
COMMENT ON COLUMN analysis_signals.signals.reason IS 'Human-readable explanation: the day''s indicator value vs the threshold (e.g. "rsi14=88.3 >= top 1% threshold 86.9 of trailing 5y window ending 2026-07-31").';
COMMENT ON COLUMN analysis_signals.signals.params IS 'Full detection parameters as JSON: mov_rsi {"rsi_window", "side", "pct", "cooldown_days"}; mov_std {"ma_window", "k", "side", "cooldown_days"}; mov_gap {"gap_window", "side", "pct", "cooldown_days"}. Values mirror the analysis_forecasts bucket keys of the matching config.';

COMMENT ON COLUMN analysis_signals.signals.is_active IS 'TRUE only for rows on the sec_type''s LATEST signal date (max(date) per sec_type — the latest date the run wrote); FALSE everywhere else. Refreshed by python -m analyze.analysis_signals after EVERY run (including --force), so exactly one date per sec_type is active at a time. Consumers (e.g. live breach monitoring) use the active rows as the current threshold set.';
