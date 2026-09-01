-- ============================================================================
--  Tables: analysis_forecasts.mov_rsi + analysis_forecasts.mov_std
--
--  The MOTIVATION (bucket-defining) tables of the forecast analysis.
--  Each row identifies ONE extreme-day bucket within the trailing
--  5-year window (stat_month - 5y, stat_month] of the code's own
--  trading days, stores the bucket's motivation stats, and links via
--  forecast_id to its RESULT row in analysis_forecasts.forecast_results
--  (1:1; the link column is NOT NULL and indexed).
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
--    mean_excess_close, mean_excess_max, max_excess_max (mov_std only,
--                  NULLable — a breach bucket may lack a usable extreme)
--                  — breach magnitude:
--                  mean_excess_close = MEAN over breach days of the
--                  fractional close excursion beyond the band
--                    upper: (price - band) / band,  lower: (band - price) / band
--                  mean_excess_max / max_excess_max = MEAN / MAX over
--                  breach days of the fractional INTRADAY excursion,
--                  high for upper breaches / low for lower breaches
--                  (ETF high/low scaled by the same adj_close/close
--                  factor as price). All fractional (0.012 = 1.2%).
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

    forecast_id      BIGINT       NOT NULL,  -- 1:1 link to analysis_forecasts.forecast_results

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

    forecast_id      BIGINT       NOT NULL,  -- 1:1 link to analysis_forecasts.forecast_results

    -- motivation cols
    is_market_hyped  BOOLEAN      NOT NULL,  -- ANY breach date inside a mov_ave_market_hypes episode (any check-in period)
    mean_excess_close NUMERIC(10,6),         -- mean fractional close excursion beyond the band over breach days
    mean_excess_max   NUMERIC(10,6),         -- mean fractional intraday excursion (high for upper / low for lower) over breach days
    max_excess_max    NUMERIC(10,6),         -- max fractional intraday excursion (high for upper / low for lower) over breach days

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
COMMENT ON COLUMN analysis_forecasts.mov_rsi.side IS 'Bucket side: top = rsi in the top pct% of the window (overbought; reversals are changes < -1%); bottom = rsi in the bottom pct% (oversold; reversals are changes > +1%).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.pct IS 'Percentile width of the bucket: 1, 5, 10 or 25 (percent). The threshold is the window''s (linear-interpolated) percentile of rsi_{W}days over non-NULL values.';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.cooldown_days IS 'Part of the PK: trading days skipped after an accepted trigger day before the next trigger may join the bucket (0 = no cooldown). Current build: 5 (config COOLDOWN_DAYS). Rows written before this column existed carry 0 (computed without cooldown).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.forecast_id IS '1:1 link to the bucket''s result row in analysis_forecasts.forecast_results (indexed; allocated by the writer).';
COMMENT ON COLUMN analysis_forecasts.mov_rsi.is_market_hyped IS 'Part of the PK: TRUE when ANY of the bucket''s dates falls inside one of the code''s analysis.mov_ave_market_hypes episodes (any min_checkin_period).';

-- ----------------------------------------------------------------------------
--  Comments — mov_std
-- ----------------------------------------------------------------------------
COMMENT ON TABLE analysis_forecasts.mov_std IS 'Bollinger-breach bucket definitions (motivation): per (code, sec_type, month, ma_window, k, side, cooldown_days, is_market_hyped) — the days within the trailing 5-year window ending at stat_month whose price closed beyond ma_{W} ± k·std_{W}days (with cooldown_days suppression after each accepted breach), plus breach-magnitude stats (mean close excursion / mean & max intraday excursion beyond the band). Results (forward changes / reversal probabilities) live in analysis_forecasts.forecast_results via forecast_id. Band inputs join from analysis.mov_ave_spreads_detail / stats.*_tech_stats. Sources: stats.*_tech_stats (ma), analysis.mov_ave_spreads_detail (std), stats.*_basic_stats closes (price, COALESCE etf_adjustment.adj_close for ETFs).';
COMMENT ON COLUMN analysis_forecasts.mov_std.sec_type IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis_forecasts.mov_std.code IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis_forecasts.mov_std.stat_month IS 'Completed month-end date. The bucket is computed over the trailing 5-year window (stat_month - 5 years, stat_month] of the code''s own trading days.';
COMMENT ON COLUMN analysis_forecasts.mov_std.ma_window IS 'MA/σ window (trading days): 5/20/60 — ma_{W} from stats.*_tech_stats, std_{W}days from analysis.mov_ave_spreads_detail.';
COMMENT ON COLUMN analysis_forecasts.mov_std.k IS 'σ multiple defining the Bollinger bound: 0.5 / 1.0 / 1.5 / 2.0 / 2.5 / 3.0.';
COMMENT ON COLUMN analysis_forecasts.mov_std.side IS 'Breach side: upper = price > ma_{W} + k·std_{W}days (reversals are changes < -1%); lower = price < ma_{W} - k·std_{W}days (reversals are changes > +1%).';
COMMENT ON COLUMN analysis_forecasts.mov_std.cooldown_days IS 'Part of the PK: trading days skipped after an accepted breach day before the next breach may join the bucket (0 = no cooldown). Current build: 5 (config COOLDOWN_DAYS). Rows written before this column existed carry 0 (computed without cooldown).';
COMMENT ON COLUMN analysis_forecasts.mov_std.forecast_id IS '1:1 link to the bucket''s result row in analysis_forecasts.forecast_results (indexed; allocated by the writer).';
COMMENT ON COLUMN analysis_forecasts.mov_std.is_market_hyped IS 'Part of the PK: TRUE when ANY breach date falls inside one of the code''s analysis.mov_ave_market_hypes episodes (any min_checkin_period).';
COMMENT ON COLUMN analysis_forecasts.mov_std.mean_excess_close IS 'Mean fractional close excursion beyond the band over breach days: (price - band)/band for side=upper, (band - price)/band for side=lower. Fractional (0.012 = 1.2% beyond the band).';
COMMENT ON COLUMN analysis_forecasts.mov_std.mean_excess_max IS 'Mean fractional INTRADAY excursion beyond the band over breach days with a usable positive extreme: (high - band)/band for side=upper, (band - low)/band for side=lower (ETF high/low scaled by the same adj_close/close factor as price). NULL when no breach day has one (CSIndex close-only history).';
COMMENT ON COLUMN analysis_forecasts.mov_std.max_excess_max IS 'MAX fractional INTRADAY excursion beyond the band over breach days with a usable positive extreme (deepest single-day spike). Same convention as mean_excess_max; >= mean_excess_max when both non-NULL.';
