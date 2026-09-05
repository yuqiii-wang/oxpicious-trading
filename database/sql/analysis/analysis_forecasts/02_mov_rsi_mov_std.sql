-- ============================================================================
--  Tables: analysis_forecasts.mov_rsi + analysis_forecasts.mov_std
--
--  The MOTIVATION (bucket-defining) tables of the forecast analysis.
--  Each row identifies ONE extreme-day bucket within the trailing
--  5-year window (stat_month - 5y, stat_month] of the code's own
--  trading days, stores the bucket's motivation stats, and links via
--  forecast_id to its RESULT rows in analysis_forecasts.forecast_results
--  (1:N — one forecast_id → 4 period rows: next / 5d / 20d / 60d;
--  the link column is NOT NULL and indexed).
--
--  Full-window gate: a code enters a stat_month only once its OWN
--  history spans the whole window (first data date <= window start) —
--  a code first listed 2020-01 first appears in the 2025-01 snapshot;
--  earlier stat_months have no rows for it (no partial-window stats).
--
--    mov_rsi — RSI extreme-percentile buckets:
--      a day is in the bucket when rsi_{W}days sits in the top pct%
--      (side=top, overbought) or bottom pct% (side=bottom, oversold)
--      of the window's non-NULL rsi_{W}days values (percentile computed
--      per code over the window, linear interpolation), AND the previous
--      accepted trigger (same bucket config) is more than cooldown_days
--      grid trading days earlier — after a trigger day, the next
--      cooldown_days trading days cannot join the bucket.
--      RSI windows mirror analysis.mov_ave_rsi:
--        6 / 10 / 14 (classic Wilder) / 20 / 60 days.
--      pct ∈ {1, 5, 10, 25} (top 1% = the highest-1%-RSI days, etc.).
--
--    mov_std — Bollinger-breach buckets:
--      a day breaches the UPPER bound when
--        price > ma_{W} + k * std_{W}days
--      and the LOWER bound when price < ma_{W} - k * std_{W}days
--      (ma_{W} from stats.{sec_type}_tech_stats, std_{W}days from
--      analysis.mov_ave_spreads_detail — the same inputs the parent
--      mov_ave_spread analysis renders), subject to the same
--      cooldown_days suppression as mov_rsi.
--      k ∈ {0.5, 1.0, 1.5, 2.0, 2.5, 3.0} (σ multiples);
--      ma_window ∈ {5, 20, 60} trading days.
--
--  Motivation columns (all part of the bucket key):
--    is_market_hyped (both, PK member) — TRUE when ANY of the bucket's
--                  dates falls inside one of the code's
--                  analysis.mov_ave_market_hypes episodes (any
--                  min_checkin_period). The underlying RSI / band values
--                  are NOT re-stored: rsi_{W}days is already in
--                  analysis.mov_ave_rsi and ma/std are in
--                  analysis.mov_ave_spreads_detail / stats.*_tech_stats,
--                  joined via (sec_type, code, date) and the bucket's
--                  rsi_window / ma_window keys.
--    Breach magnitude metrics (mov_std only) have moved to
--    analysis_forecasts.forecast_results.config JSONB:
--      mean_excess_close, mean_excess_max, max_excess_max
--      (fractional close/intraday excursion beyond the band).
--  (forward-change outcomes are NOT stored here — see forecast_results)
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis_forecasts.mov_rsi
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_forecasts.mov_rsi (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    stat_month      DATE         NOT NULL,  -- completed month-end; bucket over trailing 5y window (stat_month - 5y, stat_month]
    rsi_window      INTEGER      NOT NULL,  -- RSI window in trading days: 6/10/14/20/60
    side            TEXT         NOT NULL,  -- 'top' (overbought) | 'bottom' (oversold)
    pct             INTEGER      NOT NULL,  -- percentile width: 1 / 5 / 10 / 25
    cooldown_days   INTEGER      NOT NULL,  -- trading days skipped after an accepted trigger before the next may join: 5 (config COOLDOWN_DAYS)

    forecast_id      BIGINT       NOT NULL,  -- 1:N link → forecast_results (4 period rows)

    -- motivation cols
    is_market_hyped BOOLEAN      NOT NULL,  -- ANY bucket date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_mov_rsi PRIMARY KEY (code, sec_type, stat_month, rsi_window, side, pct, cooldown_days, is_market_hyped)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'mov_rsi', 16);

CREATE INDEX IF NOT EXISTS idx_mov_rsi_forecast_id
    ON analysis_forecasts.mov_rsi (forecast_id);

-- ----------------------------------------------------------------------------
--  Table: analysis_forecasts.mov_std
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_forecasts.mov_std (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    stat_month      DATE         NOT NULL,  -- completed month-end; bucket over trailing 5y window (stat_month - 5y, stat_month]
    ma_window       INTEGER      NOT NULL,  -- MA window in trading days: 5/20/60
    k               NUMERIC(4,2) NOT NULL,  -- σ multiple: 0.5/1.0/1.5/2.0/2.5/3.0
    side            TEXT         NOT NULL,  -- 'upper' | 'lower'
    cooldown_days   INTEGER      NOT NULL,  -- trading days skipped after an accepted breach before the next may join: 5 (config COOLDOWN_DAYS)

    forecast_id      BIGINT       NOT NULL,  -- 1:N link → forecast_results (4 period rows)

    -- motivation cols
    is_market_hyped  BOOLEAN      NOT NULL,  -- ANY breach date inside a mov_ave_market_hypes episode (any check-in period)

    CONSTRAINT pk_mov_std PRIMARY KEY (code, sec_type, stat_month, ma_window, k, side, cooldown_days, is_market_hyped)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_forecasts', 'mov_std', 16);

CREATE INDEX IF NOT EXISTS idx_mov_std_forecast_id
    ON analysis_forecasts.mov_std (forecast_id);

-- ----------------------------------------------------------------------------
--  Comments — mov_rsi
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_rsi IS 'RSI extreme-day bucket definitions (motivation): per (code, sec_type, month, rsi_window, side, pct, cooldown_days, is_market_hyped) — the days whose rsi_{W}days is in the top/bottom pct% of the trailing 5-year window ending at stat_month (with cooldown_days suppression after each accepted trigger), split by whether any bucket date is a market-hyped date. Results (forward changes / reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id. Bucket day RSI values join from analysis.mov_ave_rsi on (sec_type, code, date, rsi_window). Source: analysis.mov_ave_rsi + stats.*_basic_stats closes.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.stat_month IS 'Completed month-end date. The bucket is computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.rsi_window IS 'RSI window (trading days) whose extreme days are bucketed: 6/10/14/20/60 — mirrors analysis.mov_ave_rsi.rsi_*days.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.side IS 'Bucket side: top = rsi in the top pct% of the window (overbought; reversals are changes below the bucket''s adaptive reverse_threshold); bottom = rsi in the bottom pct% (oversold; reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.pct IS 'Percentile width of the bucket: 1, 5, 10 or 25 (percent). The threshold is the window''s (linear-interpolated) percentile of rsi_{W}days over non-NULL values.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.cooldown_days IS 'Part of the PK: trading days skipped after an accepted trigger day before the next trigger may join the bucket (0 = no cooldown). Current build: 5 (config COOLDOWN_DAYS). Rows written before this column existed carry 0 (computed without cooldown).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.forecast_id IS '1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results (indexed; allocated by the writer, shared across all 4 periods).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.is_market_hyped IS 'Part of the PK: TRUE when ANY of the bucket''s dates falls inside one of the code''s analysis.mov_ave_market_hypes episodes (any min_checkin_period).';

-- ----------------------------------------------------------------------------
--  Comments — mov_std
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_std IS 'Bollinger-breach bucket definitions (motivation): per (code, sec_type, month, ma_window, k, side, cooldown_days, is_market_hyped) — the days within the trailing 5-year window ending at stat_month whose price closed beyond ma_{W} ± k·std_{W}days (with cooldown_days suppression after each accepted breach). Breach-magnitude stats (mean_excess_close / mean_excess_max / max_excess_max) have moved to the forecast_results.config JSONB linked via forecast_id. Results (forward changes / reversal probabilities) also live in forecast_results. Band inputs join from analysis.mov_ave_spreads_detail / stats.*_tech_stats. Sources: stats.*_tech_stats (ma), analysis.mov_ave_spreads_detail (std), stats.*_basic_stats closes (price, COALESCE etf_adjustment.adj_close for ETFs).';
COMMENT ON COLUMN analysis_forecasts.mov_std.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_forecasts.mov_std.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_forecasts.mov_std.stat_month IS 'Completed month-end date. The bucket is computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.mov_std.ma_window IS 'MA/σ window (trading days): 5/20/60 — ma_{W} from stats.*_tech_stats, std_{W}days from analysis.mov_ave_spreads_detail.';
COMMENT ON COLUMN analysis_forecasts.mov_std.k IS 'σ multiple defining the Bollinger bound: 0.5 / 1.0 / 1.5 / 2.0 / 2.5 / 3.0.';
COMMENT ON COLUMN analysis_forecasts.mov_std.side IS 'Breach side: upper = price > ma_{W} + k·std_{W}days (reversals are changes below the bucket''s adaptive reverse_threshold); lower = price < ma_{W} - k·std_{W}days (reversals are changes above it).';
COMMENT ON COLUMN analysis_forecasts.mov_std.cooldown_days IS 'Part of the PK: trading days skipped after an accepted breach day before the next breach may join the bucket (0 = no cooldown). Current build: 5 (config COOLDOWN_DAYS). Rows written before this column existed carry 0 (computed without cooldown).';
COMMENT ON COLUMN analysis_forecasts.mov_std.forecast_id IS '1:N link to the bucket''s 4 period rows in analysis_forecasts.forecast_results (indexed; allocated by the writer, shared across all 4 periods).';
COMMENT ON COLUMN analysis_forecasts.mov_std.is_market_hyped IS 'Part of the PK: TRUE when ANY breach date falls inside one of the code''s analysis.mov_ave_market_hypes episodes (any min_checkin_period).';

-- ----------------------------------------------------------------------------
--  Migration: drop breach-magnitude columns (now in forecast_results.config JSONB)
-- ----------------------------------------------------------------------------
ALTER TABLE analysis_forecasts.mov_std DROP COLUMN IF EXISTS mean_excess_close;
ALTER TABLE analysis_forecasts.mov_std DROP COLUMN IF EXISTS mean_excess_max;
ALTER TABLE analysis_forecasts.mov_std DROP COLUMN IF EXISTS max_excess_max;
