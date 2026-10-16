-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below; also remove the stale analysis_identity row so the registration
-- starts clean.
DROP TABLE IF EXISTS analysis.mov_ave_momentum CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_momentum';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_momentum  (per-asset+date lookback-adaptive
--    momentum / rate-of-change)
--
--  One row per (sec_type, code, date) — one row for EVERY source-price day
--  of the parent analysis universe (mirrors mov_ave_rsi's coverage
--  contract), not only days with a regime-state row.
--
--  MOMENTUM DEFINITION (the time-series ROC convention, same signed
--  fractional-ratio units as the mov_ave gap columns):
--      momentum[n][t] = price[t] / price[t-n] - 1
--  computed over the parent mov_ave_spread price column (ETF =
--  COALESCE(etf_adjustment.adj_close, etf_basic_stats.close); index =
--  index_basic_stats.close; stock = stock_basic_stats.close), per code
--  ordered by trading date. NULL until the code has n prior rows (or when
--  either price leg is NULL / the base is 0).
--
--  TWO FIXED lookbacks — the stable anchors requested everywhere:
--      momentum_5days   n = 5   (one trading week)
--      momentum_20days  n = 20  (one trading month)
--
--  THREE ADAPTIVE lookbacks — short / mid / long terms whose lookback n
--  is chosen SELF-ADAPTIVELY BY VOLATILITY per (code, regime): each day
--  carries the regime it was classified into by stats.market_regimes
--  (the 4-state registry; calm/hot/panic/quiet), and the lookbacks are
--  derived from that (code, regime) slice's volatility level via
--  EQUAL-EXPECTED-MOVE targeting (the volatility-time formulation):
--
--      sigma(code, regime) = median of stats.market_regimes.vol
--                            (sqrt(EWMA(ret_1d^2, span=20)) — the SAME
--                            vol definition the regime classification
--                            itself z-scores) over the code's days in
--                            that regime, full history
--      n_term = round( (target_move_term / sigma)^2 )
--             = the number of trading days after which one standard
--               deviation of cumulative return equals the term's target
--               move — high-vol codes/regimes get SHORTER lookbacks
--               (prices go "stale" faster), low-vol ones longer.
--
--  Calibration (recorded here; the constants live in
--  analysis/mov_ave_spread/steps/momentum.py and can be retuned there):
--      term    target_move   clip bounds      typical calm A-share index
--      short   3%            [5, 60]          sigma~1.2%/d -> ~6d
--      mid     6%            [20, 120]                          ~25d
--      long    12%           [60, 255]                         ~100d
--  The 5/20-day anchors double as the short/mid FLOORS, so the adaptive
--  lookbacks never drop below the fixed references. After clipping, the
--  ordering n_short <= n_mid <= n_long is enforced (cumulative max).
--
--  Fallback chain when a (code, regime) slice is too thin to trust its
--  own median (< 20 non-null vol days): the code's overall median vol;
--  when even that is unavailable (no vol rows at all): a 2%/day default.
--
--  Columns:
--    regime      the day's stats.market_regimes regime code — the label
--                the adaptive lookbacks were derived FOR. Days with no
--                regime row (registry lag) store calm (0) with NULL
--                vol_regime evidence, matching the registry's own
--                no-state-day convention.
--    vol_regime  the sigma(code, regime) the lookbacks were derived from
--                (audit evidence; NULL on fallback-default slices).
--
--  LOOK-AHEAD NOTE: sigma(code, regime) aggregates the code's FULL
--  history in that regime, so a past day's stored lookbacks can shift
--  retroactively when new days arrive (the rsi_ranks precedent — the
--  classification input itself is trailing/shift-1, but the per-slice
--  median is full-history). Consequence: REBUILD SEMANTICS are wholesale
--  per sec_type on every run that computes momentum (DELETE the run's
--  scope, CSV-COPY the full recompute) — never incrementally upserted.
--
--  NO FK to stats.market_regimes (the NO-FK derived-data design of
--  01_mov_ave_spreads_detail.sql) — the momentum step INNER-LEFT-joins
--  the registry in-memory on (code, date).
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_momentum (
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,
    regime          SMALLINT     NOT NULL,  -- label code (static.label_dim 'regime'): 0=calm 1=hot 2=panic 3=quiet

    -- the (code, regime) median EWMA vol the lookbacks were derived from
    vol_regime      DOUBLE PRECISION,

    -- adaptive lookbacks in effect that day (trading days)
    lb_short        SMALLINT     NOT NULL,  -- target move 3%,  clipped to [5, 60]
    lb_mid          SMALLINT     NOT NULL,  -- target move 6%,  clipped to [20, 120]
    lb_long         SMALLINT     NOT NULL,  -- target move 12%, clipped to [60, 255]

    -- fixed-anchor momentum (signed fractional; NULL until n prior rows)
    momentum_5days  NUMERIC(10,6),
    momentum_20days NUMERIC(10,6),

    -- momentum at the day's adaptive lookbacks (NULL until lb prior rows)
    momentum_short  NUMERIC(10,6),
    momentum_mid    NUMERIC(10,6),
    momentum_long   NUMERIC(10,6),

    CONSTRAINT pk_mov_ave_momentum PRIMARY KEY (code, sec_type, date),
    CONSTRAINT ck_mov_ave_momentum_lb
        CHECK (lb_short BETWEEN 5 AND 60 AND lb_mid BETWEEN 20 AND 120
           AND lb_long BETWEEN 60 AND 255 AND lb_short <= lb_mid
           AND lb_mid <= lb_long),
    CONSTRAINT ck_mov_ave_momentum_regime
        CHECK (regime BETWEEN 0 AND 3)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed on code — same convention as the
-- sibling mov_ave_rsi tables so the wholesale DELETE per sec_type and the
-- per-code API reads walk the same partitioning.
SELECT public.create_hash_partitions('analysis', 'mov_ave_momentum', 16);

-- (sec_type, code, date) index — the sec_type-leading read shape the
-- /extras momentum group uses (one code's momentum history per request);
-- same rationale as the mov_ave_rsi index.
CREATE INDEX IF NOT EXISTS idx_mov_ave_momentum_sec_type_code_date
    ON analysis.mov_ave_momentum (sec_type, code, date);

COMMENT ON TABLE  analysis.mov_ave_momentum           IS 'Lookback-adaptive momentum (rate of change): one row per (code, sec_type, date). momentum[n][t] = price[t]/price[t-n] - 1 over the parent mov_ave_spread price column. TWO FIXED lookbacks (momentum_5days, momentum_20days — the stable anchors) plus THREE ADAPTIVE lookbacks whose n is chosen self-adaptively by volatility per (code, regime): n_term = round((target_move_term / sigma(code,regime))^2) — equal-expected-move / volatility-time targeting over the median stats.market_regimes.vol of the code''s days in that regime (high vol -> shorter lookbacks). short target 3% clipped [5,60]; mid 6% [20,120]; long 12% [60,255]; ordering enforced. lb_* record the day''s effective lookbacks; vol_regime the sigma they were derived from. sigma is FULL-HISTORY per slice, so past lookbacks shift with new data — the table is REBUILT WHOLESALE per sec_type on every run that computes it (the rsi_ranks precedent). Built by analysis.mov_ave_spread (steps/momentum.py).';
COMMENT ON COLUMN analysis.mov_ave_momentum.sec_type        IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_momentum.code            IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_momentum.date            IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_momentum.regime          IS 'The day''s regime as its static.label_dim (''regime'' domain) SMALLINT code: 0 calm, 1 hot, 2 panic, 3 quiet (from stats.market_regimes; the label-code contract). Days with no registry row (build lag) store 0 (calm) with NULL vol_regime — the registry''s own no-state-day convention.';
COMMENT ON COLUMN analysis.mov_ave_momentum.vol_regime       IS 'The sigma(code, regime) the day''s adaptive lookbacks were derived from: the median of stats.market_regimes.vol (sqrt(EWMA(ret_1d^2, span=20)) — the same vol the regime classification z-scores) over the code''s full-history days in that regime. NULL when the slice fell back to the code-level median or the 2%/day default (audit: compare across rows of the same code), or on days whose regime row carried no vol yet.';
COMMENT ON COLUMN analysis.mov_ave_momentum.lb_short        IS 'Short-term adaptive lookback in trading days: round((0.03 / sigma(code,regime))^2) clipped to [5, 60]. Calm index (~1.2%/d sigma) -> ~6d; elevated-vol regimes clip to the 5d anchor floor.';
COMMENT ON COLUMN analysis.mov_ave_momentum.lb_mid          IS 'Mid-term adaptive lookback in trading days: round((0.06 / sigma(code,regime))^2) clipped to [20, 120]. Calm index -> ~25d.';
COMMENT ON COLUMN analysis.mov_ave_momentum.lb_long         IS 'Long-term adaptive lookback in trading days: round((0.12 / sigma(code,regime))^2) clipped to [60, 255]. Calm index -> ~100d.';
COMMENT ON COLUMN analysis.mov_ave_momentum.momentum_5days  IS 'Fixed-anchor momentum: price[t]/price[t-5] - 1 (signed fractional ratio, e.g. 0.05 = +5%). NULL until the code has 5 prior rows or when a price leg is NULL/0.';
COMMENT ON COLUMN analysis.mov_ave_momentum.momentum_20days IS 'Fixed-anchor momentum: price[t]/price[t-20] - 1. NULL until 20 prior rows (one trading month).';
COMMENT ON COLUMN analysis.mov_ave_momentum.momentum_short  IS 'Adaptive momentum at the day''s lb_short lookback: price[t]/price[t-lb_short] - 1. The lookback varies BY DAY (regime-dependent), so this column is NOT comparable to a fixed-window momentum across regime boundaries — read it together with lb_short. NULL until lb_short prior rows.';
COMMENT ON COLUMN analysis.mov_ave_momentum.momentum_mid    IS 'Adaptive momentum at the day''s lb_mid lookback: price[t]/price[t-lb_mid] - 1. NULL until lb_mid prior rows.';
COMMENT ON COLUMN analysis.mov_ave_momentum.momentum_long   IS 'Adaptive momentum at the day''s lb_long lookback: price[t]/price[t-lb_long] - 1. NULL until lb_long prior rows.';
