-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below; also remove the stale analysis_identity row so the registration
-- starts clean.
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_ohlc CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_ohlc';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_detail_ohlc  (OHLC summary per period,
--  LONG format)
--    One row per (sec_type, code, date, period): today_close plus rolling
--    OHLC stats for that period. period ∈ {20, 60, 120, 255, 500, 750,
--    1275} trading days. The per-window column names collapse into one
--    generic *_over_period column set — no duplicated names per window.
--
--  Columns:
--    sec_type, code, date, period       — PK; identifies the asset universe
--                                         (etf vs index), ticker, date, and
--                                         the rolling-window size
--    today_close                        — close price on `date`
--    open_over_period      — open price on the period-th trading day
--                            before `date`
--    high_over_period      — top-high anchor: MAXIMUM valid CLOSE in the
--                            1st HALF of the window (value = that anchor
--                            date's close)
--    low_over_period       — lowest-low anchor: MINIMUM valid CLOSE in the
--                            1st half (value = that anchor date's close)
--    high_2nd_over_period  — second-high anchor: MAXIMUM valid CLOSE in
--                            [top_high_pos + gap, window_end] (2nd date
--                            is always later than the top date — the
--                            gap is enforced constructively, so the
--                            roof line runs forward in time)
--                            (value = that anchor date's INTRADAY HIGH)
--    low_2nd_over_period   — second-low anchor: MINIMUM valid CLOSE in
--                            [top_low_pos + gap, window_end] (floor
--                            line runs forward in time)
--                            (value = that anchor date's INTRADAY LOW)
--
--  HALF-SPLIT 1st ANCHORS + DYNAMIC 2nd RANGE: the window
--  [date-period+1, date] is cut in half — h = L // 2 with L the window
--  length in trading-day positions (for odd L the 2nd half gets the
--  extra day); the 1st extreme is the max/min valid CLOSE of the 1st
--  half. The 2nd extreme is NOT confined to the 2nd half — its search
--  range starts `gap` trading days AFTER the 1st anchor, where
--  gap = ceil(0.20*period) with a 20-trading-day floor for periods
--  >= 60 (period 20 keeps the pure 20% gap: a 20td floor would push
--  the search start past the window end for every row). Ties go to the
--  earliest date; NaN closes are skipped. Columns are NULL when the
--  window has fewer than 2 positions or the half holds no valid close;
--  when the 1st anchor is too close to the window end (top + gap >
--  window_end), only the 2nd anchor is NULLed while the 1st stays
--  valid.
--  MIGRATION NOTE (2026-09): the gap was ceil(0.10*period) before —
--  rows written by the 10% rule must be recomputed via a --force
--  rebuild of the OHLC step (find_ohlc_repair_dates cannot detect
--  them).
--  Populated by the internal OHLC step of `analyze.mov_ave_spread`
--  (see ohlc.py). Reuses the same source DataFrame as the parent
--  pipeline — no second DB round-trip.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_detail_ohlc (
    sec_type          TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,

    today_close       NUMERIC(18,6) NOT NULL,
    period            INTEGER      NOT NULL,  -- window size in trading days

    open_over_period          NUMERIC(18,6),
    high_over_period          NUMERIC(18,6),
    high_date_over_period    DATE,
    low_over_period           NUMERIC(18,6),
    low_date_over_period     DATE,
    high_2nd_over_period          NUMERIC(18,6),
    high_2nd_date_over_period     DATE,
    low_2nd_over_period           NUMERIC(18,6),
    low_2nd_date_over_period     DATE,
    high_line_slope_over_period NUMERIC(18,6),
    low_line_slope_over_period  NUMERIC(18,6),

    
    CONSTRAINT pk_mov_ave_spreads_detail_ohlc
        PRIMARY KEY (code, sec_type, date, period)
) PARTITION BY HASH (code);

-- Native hash partitions (32) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_detail_ohlc', 32);

-- LONG format: one row per (code, sec_type, date, period). The per-window
-- columns collapse into a single generic *_over_period column set keyed by
-- `period` ∈ {20, 60, 120, 255, 500, 750, 1275} trading days.
COMMENT ON TABLE  analysis.mov_ave_spreads_detail_ohlc              IS 'OHLC detail (LONG format): one row per (code, sec_type, date, period) with today_close + rolling-window anchors per period ∈ {20,60,120,255,500,750,1275} trading days. HALF-SPLIT 1st ANCHORS + DYNAMIC 2nd RANGE: the period window [date-period+1, date] is cut in half — h = L // 2 in trading-day positions (for odd L the 2nd half gets the extra day). 1st anchors (high_over_period/low_over_period): the MAXIMUM/MINIMUM valid CLOSE of the 1st half (ties -> earliest date; value = anchor date close). 2nd anchors (high_2nd_over_period/low_2nd_over_period): the MAXIMUM/MINIMUM valid CLOSE found in [1st_anchor_position + gap, window_end] where gap = ceil(0.20*period) with a 20-trading-day floor for periods >= 60 (period 20 keeps the pure 20% gap) — the 2nd anchor is NOT confined to the 2nd half. The time-distance gap is enforced CONSTRUCTIVELY by shifting the 2nd anchor search range, guaranteeing |sec_pos - top_pos| >= gap wherever both exist. value = anchor date INTRADAY high/low. NaN closes are skipped; a half with no valid close NULLs its 1st anchor; when the 1st anchor is too close to the window end (top + gap > window_end), only the 2nd anchor is NULLed while the 1st stays valid. 2nd anchor dates are always strictly after the 1st anchor date. DATE columns record the anchor dates. sec_type ∈ {etf, index, stock}. Source: same DataFrame as mov_ave_spread parent pipeline (no second DB round-trip).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.sec_type     IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.code        IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.date        IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.today_close IS 'Close price on `date` (COALESCE(adj_close, close) for ETFs; close for index/stock). NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.period      IS 'Rolling-window size in trading days: one of {20, 60, 120, 255, 500, 750, 1275}. Part of the PK — the long format stores one row per (code, sec_type, date, period).';

COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.open_over_period   IS 'Open price on the period-th trading day before `date`. NULL if fewer than `period` prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_over_period   IS 'Top-high anchor: the MAXIMUM valid CLOSE in the 1st HALF of the period window ([date-period+1, date] cut in half, h = L // 2; ties -> earliest date); value = the anchor date close. NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_date_over_period IS 'Business date of the top-high anchor (max valid CLOSE of the window''s 1st half). NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_over_period    IS 'Lowest-low anchor: the MINIMUM valid CLOSE in the 1st HALF of the period window (ties -> earliest date); value = the anchor date close. NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_date_over_period IS 'Business date of the lowest-low anchor (min valid CLOSE of the window''s 1st half). NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_2nd_over_period IS 'Second-high anchor: the MAXIMUM valid CLOSE found in [top_high_pos + gap, window_end] where gap = ceil(0.20*period) with a 20-trading-day floor for periods >= 60 (period 20 keeps the pure 20% gap) — the time-distance gap is enforced CONSTRUCTIVELY. NOT confined to the 2nd half; the search range starts from the gap AFTER the 1st anchor. value = the anchor date INTRADAY HIGH. NULL when the dynamic range holds no valid close OR when the 1st anchor is too close to the window end.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_2nd_date_over_period IS 'Business date of the second-high anchor. Always strictly after the top-high anchor date. NULL when the dynamic range holds no valid close OR when the 1st anchor is too close to the window end.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_2nd_over_period  IS 'Second-low anchor: the MINIMUM valid CLOSE found in [top_low_pos + gap, window_end] where gap = ceil(0.20*period) with a 20-trading-day floor for periods >= 60 (period 20 keeps the pure 20% gap) — the time-distance gap is enforced CONSTRUCTIVELY. NOT confined to the 2nd half. value = the anchor date INTRADAY LOW. NULL when the dynamic range holds no valid close OR when the 1st anchor is too close to the window end.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_2nd_date_over_period IS 'Business date of the second-low anchor. Always strictly after the lowest-low anchor date. NULL when the dynamic range holds no valid close OR when the 1st anchor is too close to the window end.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.high_line_slope_over_period IS 'Slope of the roof line through the two high anchors of this period (high_over_period close -> high_2nd_over_period intraday high), in price units per trading day: (high_2nd_over_period - high_over_period) / (trading days between the two anchor dates). Signed; the 2nd anchor always lies at least gap = ceil(0.20*period) days (20td floor for periods >= 60) after the top. NULL when either anchor is absent.';
COMMENT ON COLUMN analysis.mov_ave_spreads_detail_ohlc.low_line_slope_over_period  IS 'Slope of the floor line through the two low anchors of this period (low_over_period close -> low_2nd_over_period intraday low), in price units per trading day. Signed; the 2nd anchor always lies at least gap = ceil(0.20*period) days (20td floor for periods >= 60) after the bottom. NULL when either anchor is absent.';
