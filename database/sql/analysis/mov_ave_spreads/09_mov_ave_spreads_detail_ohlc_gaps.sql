-- ============================================================================
--  mov_ave_spread suite — OHLC intraday GAP metrics.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below; also remove the stale analysis_identity row so the registration
-- starts clean.
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_ohlc_gaps CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_ohlc_gaps';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail_ohlc_gaps  (WIDE: one row per
--  sec_type + code + date; 8 raw OHLC gap columns + the 2026-09-27
--  PATTERN block: wick top-decile flags + the classified day pattern)
--
--  Daily OHLC intraday gap metrics — the quantified 高开低走/低开高走/
--  冲高回落/探底回升 feature set from the 2026-09 OHLC studies
--  (temp_scripts/study_ohlc_gap_vs_extreme.py + study_ohlc_patterns.py).
--
--  PATTERN BLOCK (2026-09-27 — computed in ohlc_gaps.compute_ohlc_gaps,
--  consumed by the analysis_signals ohlcv-boost weights
--  (analysis/analysis_signals/engines/ohlcv_boost) and the
--  MA-Spread "Top-10% OHLC Days" chart group):
--    uw_top10   upper wick >= the code's trailing-252d 90th percentile
--               (>= 120 obs) — the spike-fade shape
--    lw_top10   lower wick likewise — the flush-recover shape
--    pattern    the day's classified pattern, precedence FIRST-MATCH:
--               spike_fade (冲高回落, uw_top10) → flush_recover
--               (探底回升, lw_top10) → the gap×candle quadrants at the
--               ±0.1% deadband (gap_up_advance 高开高走 green /
--               gap_up_decline 高开低走 red / gap_down_advance 低开高走
--               green / gap_down_decline 低开低走 red) → neutral
--               (|gap| < deadband, anatomy known). NULL = anatomy
--               unknown (the code's first valid day / missing inputs) —
--               deliberately distinct from 'neutral'. Flags and the
--               pattern are computed on UNROUNDED values before the
--               NUMERIC(10,6) storage rounding.
--
--  ESTIMATED-DATA EXCLUSION: source rows with is_ohl_estimated = TRUE
--  (stats.{etf,index,stock}_basic_stats) are excluded ENTIRELY; close-less
--  rows (close IS NULL — e.g. former estimated-close rows nulled by the
--  2026-09-30 is_close_estimated retirement) never reach the frame (the
--  parent fetch's close IS NOT NULL filter). The exclusion is also a
--  prev_close break: prev_close references the last VALID (non-estimated)
--  close of the same code, so overnight legs never cross a synthetic row.
--  Dates whose every source row is estimated therefore have no rows here
--  and remain permanently "missing" to incremental detection (harmless:
--  0 rows are (re)inserted).
--
--  Normalization: ETFs use the parent pipeline's ADJUSTED series
--  (COALESCE(adj_*, raw) for open/high/low/close); index/stock use raw
--  OHLC. Same-date ratios are invariant to the multiplicative adjustment;
--  cross-date legs (gap_open vs prev_close) are total-return gaps.
--
--  prev_close = previous VALID close per (sec_type, code) ordered by date.
--  All gap columns are signed FRACTIONS (not %). NULL when any input is
--  NaN or the denominator is zero/denormalized (|den| < 1e-12).
--
--    gap_open   = open / prev_close - 1            (overnight leg; 高开/低开)
--    intra      = close / open - 1                 (intraday leg; 高走/低走)
--    uw         = (high - GREATEST(open, close)) / prev_close
--                                                 (upper wick; 冲高回落)
--    lw         = (LEAST(open, close) - low) / prev_close
--                                                 (lower wick; 探底回升)
--    rng        = (high - low) / prev_close        (daily range)
--    up_gap     = (high - close) / close           (close-relative upper gap)
--    dn_gap     = (close - low) / close            (close-relative lower gap)
--    close_pos  = (close - low) / (high - low)     (close location in the
--                                                 day's range; NULL when
--                                                 high = low)
--
--  Populated by the internal OHLC-gaps step of `analysis.mov_ave_spread`
--  (see ohlc_gaps.py). Incremental upsert by missing dates; --force
--  truncates first.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail_ohlc_gaps (
    sec_type          SMALLINT     NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    -- 8 raw daily gap columns (signed fractions)
    gap_open          NUMERIC(10,6),
    intra             NUMERIC(10,6),
    uw                NUMERIC(10,6),
    lw                NUMERIC(10,6),
    rng               NUMERIC(10,6),
    up_gap            NUMERIC(10,6),
    dn_gap            NUMERIC(10,6),
    close_pos         NUMERIC(10,6),

    -- 2026-09-27 PATTERN block (see the header comment): wick
    -- top-decile flags + the classified day pattern. NULL flags = the
    -- trailing window is short (< 120 obs) or the wick input is NULL;
    -- NULL pattern = unknown anatomy.
    uw_top10          BOOLEAN,
    lw_top10          BOOLEAN,
    pattern           TEXT,

    CONSTRAINT pk_mov_ave_spreads_detail_ohlc_gaps
        PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_detail_ohlc_gaps', 16);

-- 2026-09-27 pattern block: idempotent ADDs for existing installations
-- (fresh installs create the columns above). Existing rows stay NULL
-- until the next incremental / --force run of analysis.mov_ave_spread.
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps ADD COLUMN IF NOT EXISTS uw_top10 BOOLEAN;
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps ADD COLUMN IF NOT EXISTS lw_top10 BOOLEAN;
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps ADD COLUMN IF NOT EXISTS pattern TEXT;

-- The pattern vocabulary (NULL = unknown anatomy — passes the CHECK).
SELECT public.ensure_check_constraint(
    'analysis.mov_ave_spreads_detail_ohlc_gaps',
    'chk_mov_ave_spreads_detail_ohlc_gaps_pattern',
    $chk$pattern IN ('gap_up_advance', 'gap_up_decline',
                     'gap_down_advance', 'gap_down_decline',
                     'spike_fade', 'flush_recover', 'neutral')$chk$);
SELECT public.validate_pending_checks('analysis');

-- 2026-09-27 EMA5 removal: the 8 per-gap EMA5 smoothing columns were
-- never consumed downstream (no view / analysis / UI reads them) —
-- dropped here so existing installations lose the columns; fresh
-- installs never create them.
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps DROP COLUMN IF EXISTS ema5_gap_open;
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps DROP COLUMN IF EXISTS ema5_intra;
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps DROP COLUMN IF EXISTS ema5_uw;
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps DROP COLUMN IF EXISTS ema5_lw;
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps DROP COLUMN IF EXISTS ema5_rng;
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps DROP COLUMN IF EXISTS ema5_up_gap;
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps DROP COLUMN IF EXISTS ema5_dn_gap;
ALTER TABLE analysis.mov_ave_spreads_detail_ohlc_gaps DROP COLUMN IF EXISTS ema5_close_pos;

COMMENT ON TABLE  analysis.mov_ave_spreads_detail_ohlc_gaps IS 'OHLC intraday gap detail (WIDE format): one row per (code, sec_type, date) with 8 raw daily OHLC gap columns. ESTIMATED-DATA EXCLUSION: source rows with is_ohl_estimated TRUE are excluded entirely; close-less rows (close IS NULL) never reach the frame (parent fetch filter); prev_close references the last VALID close per (sec_type, code) (overnight legs never cross a synthetic row). prev_close = previous valid close ordered by date (ETFs: adjusted close — COALESCE(adj_close, close); total-return gaps). gap_open = open/prev_close - 1 (overnight leg, 高开/低开); intra = close/open - 1 (intraday leg, 高走/低走); uw = (high - GREATEST(open, close))/prev_close (upper wick, 冲高回落); lw = (LEAST(open, close) - low)/prev_close (lower wick, 探底回升); rng = (high - low)/prev_close (daily range); up_gap = (high - close)/close; dn_gap = (close - low)/close; close_pos = (close - low)/(high - low) (NULL when high = low). All gaps are signed fractions; NULL when any input is NaN or the denominator is zero/denormalized (|den| < 1e-12). Feature set from the 2026-09 OHLC studies (temp_scripts/study_ohlc_gap_vs_extreme.py, study_ohlc_patterns.py). Source: same DataFrame as the mov_ave_spread parent pipeline (no second DB round-trip) with the estimated-row filter applied by the step. 2026-09-27 pattern block: uw_top10 / lw_top10 (wick >= the code's trailing-252d 90th percentile, >= 120 obs) + pattern (spike_fade / flush_recover / the gap_up-gap_down advance-decline quadrants / neutral; NULL = unknown anatomy) — computed on unrounded values, consumed by the analysis_signals ohlcv-boost weights (analysis/analysis_signals/engines/ohlcv_boost) and the MA-Spread Top-10% OHLC Days chart group.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.sec_type IS 'Security type as its stats.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.code      IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.date      IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.gap_open  IS 'open / prev_close - 1 — overnight gap leg (高开 positive / 低开 negative). prev_close = previous VALID (non-estimated) close per (sec_type, code) ordered by date; NULL on a code''s first valid row, when open is NULL, or |prev_close| < 1e-12.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.intra     IS 'close / open - 1 — intraday leg (高走 positive / 低走 negative). NULL when open/close is NULL or |open| < 1e-12.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.uw        IS '(high - GREATEST(open, close)) / prev_close — upper wick in return units (冲高回落 spike-fade magnitude; 0 when close is at/above the high). NULL when prev_close invalid.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.lw        IS '(LEAST(open, close) - low) / prev_close — lower wick in return units (探底回升 flush-recover magnitude; 0 when close is at/below the low). NULL when prev_close invalid.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.rng       IS '(high - low) / prev_close — daily range in return units. NULL when prev_close invalid.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.up_gap    IS '(high - close) / close — close-relative upper gap (rejection depth of the close below the day high). NULL when close invalid.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.dn_gap    IS '(close - low) / close — close-relative lower gap (depth of the close above the day low). NULL when close invalid.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.close_pos IS '(close - low) / (high - low) — close location within the day''s range: 0 = close at the low, 1 = close at the high. NULL when high = low or any input is NaN.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.uw_top10 IS 'TRUE when the upper wick (uw) is >= the code''s trailing-252d 90th percentile of uw (minimum 120 trailing observations) — the spike-fade shape (冲高回落). Computed on UNROUNDED values in the ohlc_gaps step (study_ohlc_patterns'' rolling rank(pct=True) >= 0.90 condition). NULL when the trailing window is short or the wick input is NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.lw_top10 IS 'TRUE when the lower wick (lw) is >= the code''s trailing-252d 90th percentile of lw (minimum 120 trailing observations) — the flush-recover shape (探底回升). Same computation/NULL semantics as uw_top10.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc_gaps.pattern IS 'The day''s classified OHLC pattern — precedence FIRST-MATCH: spike_fade (冲高回落, uw_top10) → flush_recover (探底回升, lw_top10) → the gap×candle quadrants at the ±0.1% deadband (gap_up_advance 高开高走 / gap_up_decline 高开低走 / gap_down_advance 低开高走 / gap_down_decline 低开低走 — gap_open vs deadband, intra sign) → neutral (|gap| < deadband, anatomy known). NULL = anatomy unknown (the code''s first valid day / NULL inputs) — deliberately distinct from neutral. Classified on UNROUNDED values in the ohlc_gaps step; consumed by the analysis_signals ohlcv-boost weights (the measured-edge ladder — analysis/analysis_signals/engines/ohlcv_boost) and the MA-Spread "Top-10% OHLC Days" chart group.';
