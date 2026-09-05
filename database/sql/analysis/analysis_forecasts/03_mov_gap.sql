-- ============================================================================
--  Table: analysis_forecasts.mov_gap
--
--  Third MOTIVATION (bucket-defining) table of the forecast analysis:
--  short-term price-gap (N-day return) extreme-percentile buckets —
--  the exact mov_rsi machinery applied to the gap_{W}days columns
--  (W-day price return (price[t] - price[t-W]) / price[t-W], stored in
--  analysis.mov_ave_rsi alongside the RSI columns).
--
--  A day joins the bucket when gap_{W}days sits in the top pct%
--  (side=top — a sharp W-day rally, overbought) or bottom pct%
--  (side=bottom — a sharp W-day selloff, oversold) of the trailing
--  5-year window's non-NULL gap_{W}days values (linear-interpolated
--  percentile per code), subject to the same cooldown_days suppression
--  as mov_rsi / mov_std. gap_window ∈ {2, 3} (the analysis.mov_ave_rsi
--  gap_2days / gap_3days columns); pct ∈ {1, 5, 10, 25}.
--
--  Full-window gate + hype split + forecast_id link: identical to
--  mov_rsi (see 02_mov_rsi_mov_std.sql). Results (forward changes /
--  reversal probabilities) live in analysis_forecasts.forecast_results
--  via forecast_id (1:N — one forecast_id → 4 period rows).
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis_forecasts.mov_gap (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    stat_month      DATE         NOT NULL,  -- completed month-end; bucket over trailing 5y window (stat_month - 5y, stat_month]
    gap_window      INTEGER      NOT NULL,  -- gap window in trading days: 2/3 (gap_{W}days N-day return)
    side            TEXT         NOT NULL,  -- 'top' (sharp rally) | 'bottom' (sharp selloff)
    pct             INTEGER      NOT NULL,  -- percentile width: 1 / 5 / 10 / 25
    cooldown_days   INTEGER      NOT NULL,  -- trading days skipped after an accepted trigger before the next may join: 5 (config COOLDOWN_DAYS)

    forecast_id      BIGINT       NOT NULL,  -- 1:N link → forecast_results (4 period rows)

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_mov_gap PRIMARY KEY (code, sec_type, stat_month, gap_window, side, pct, cooldown_days, is_market_hyped)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'mov_gap', 16);

CREATE INDEX IF NOT EXISTS idx_mov_gap_forecast_id
    ON analysis_forecasts.mov_gap (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_gap IS 'Short-term price-gap (N-day return) extreme-day bucket definitions (motivation): per (code, sec_type, month, gap_window, side, pct, cooldown_days, is_market_hyped) — the days whose gap_{W}days = (price[t]-price[t-W])/price[t-W] is in the top pct% (side=top, sharp W-day rally) or bottom pct% (side=bottom, sharp W-day selloff) of the trailing 5-year window ending at stat_month (with cooldown_days suppression after each accepted trigger), split by whether any bucket date is a market-hyped date. gap_window ∈ {2, 3} mirrors analysis.mov_ave_rsi.gap_2days / gap_3days. Results (forward changes / reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id. Source: analysis.mov_ave_rsi (gap columns).';
COMMENT ON COLUMN analysis_forecasts.mov_gap.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_forecasts.mov_gap.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_forecasts.mov_gap.stat_month IS 'Completed month-end date. The bucket is computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.mov_gap.gap_window IS 'N-day price-return window (trading days) whose extreme days are bucketed: 2/3 — mirrors analysis.mov_ave_rsi.gap_2days / gap_3days.';
COMMENT ON COLUMN analysis_forecasts.mov_gap.side IS 'Bucket side: top = gap_{W}days in the top pct% of the window (sharp rally; reversals are changes below the bucket''s adaptive reverse_threshold); bottom = gap_{W}days in the bottom pct% (sharp selloff; reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_gap.pct IS 'Percentile width of the bucket: 1, 5, 10 or 25 (percent). The threshold is the window''s (linear-interpolated) percentile of gap_{W}days over non-NULL values.';
COMMENT ON COLUMN analysis_forecasts.mov_gap.cooldown_days IS 'Part of the PK: trading days skipped after an accepted trigger day before the next trigger may join the bucket. Current build: 5 (config COOLDOWN_DAYS).';
COMMENT ON COLUMN analysis_forecasts.mov_gap.forecast_id IS '1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results (indexed; allocated by the writer, shared across all 4 periods).';
COMMENT ON COLUMN analysis_forecasts.mov_gap.is_market_hyped IS 'Part of the PK: TRUE when ANY of the bucket''s dates falls inside one of the code''s analysis.mov_ave_market_hypes episodes (any min_checkin_period).';
