-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below; also remove the stale analysis_identity row so the registration
-- starts clean. The 2026-10 rename drops the legacy table name too
-- (mov_ave_spreads_detail_ohlc) — the data is derived and is fully
-- recomputed by the next analysis.mov_ave_spread run (the incremental
-- missing-date check sees every date missing after the DROP). The same
-- 2026-10 rework SPLIT change/days_since_last_extreme (the move since
-- the latest-dated of the four anchors) into the per-side
-- change/days_since_last_high + change/days_since_last_low columns —
-- the DROP makes the old rows vanish with the table.
DROP TABLE IF EXISTS analysis.mov_ave_spreads_roof_and_floor_lines CASCADE;
DROP TABLE IF EXISTS analysis.mov_ave_spreads_detail_ohlc CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_ohlc';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_roof_and_floor_lines  (renamed 2026-10
--  from mov_ave_spreads_detail_ohlc — roof/floor anchors + lines; LONG
--  format)
--    One row per (sec_type, code, date, period): today_close plus rolling
--    roof/floor anchor stats for that period. period ∈ {20, 60, 120, 255,
--    500, 750, 1275} trading days. The per-window column names collapse
--    into one generic *_over_period column set — no duplicated names per
--    window.
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
--    change_since_last_high — today_close MINUS the value of the LAST
--                            HIGH anchor of the period (the LATER-dated
--                            of the two high anchors — the 2nd whenever
--                            present, else the 1st; see the column
--                            comment); sign indicates growth or drop
--    days_since_last_high   — trading days between `date` and that
--                            last high anchor date
--    change_since_last_low  — today_close MINUS the value of the LAST
--                            LOW anchor (the mirror rule on the low
--                            side)
--    days_since_last_low    — trading days between `date` and that
--                            last low anchor date
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
--  Populated by the internal OHLC step of `analysis.mov_ave_spread`
--  (see ohlc.py). Reuses the same source DataFrame as the parent
--  pipeline — no second DB round-trip.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_roof_and_floor_lines (
    sec_type          SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
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
    low_2nd_date_over_period      DATE,
    high_line_slope_over_period NUMERIC(18,6),
    low_line_slope_over_period  NUMERIC(18,6),

    change_since_last_high NUMERIC(18,6),
    days_since_last_high   INTEGER,
    change_since_last_low NUMERIC(18,6),
    days_since_last_low   INTEGER,

    CONSTRAINT pk_mov_ave_spreads_roof_and_floor_lines
        PRIMARY KEY (code, sec_type, date, period)
) PARTITION BY HASH (code);

-- Native hash partitions (32) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_roof_and_floor_lines', 32);

-- LONG format: one row per (code, sec_type, date, period). The per-window
-- columns collapse into a single generic *_over_period column set keyed by
-- `period` ∈ {20,60,120,255,500,750,1275} trading days.
COMMENT ON TABLE  analysis.mov_ave_spreads_roof_and_floor_lines              IS 'Roof/floor anchor detail, renamed 2026-10 from mov_ave_spreads_detail_ohlc (LONG format): one row per (code, sec_type, date, period) with today_close + rolling-window anchors per period ∈ {20,60,120,255,500,750,1275} trading days. HALF-SPLIT 1st ANCHORS + DYNAMIC 2nd RANGE: the period window [date-period+1, date] is cut in half — h = L // 2 in trading-day positions (for odd L the 2nd half gets the extra day). 1st anchors (high_over_period/low_over_period): the MAXIMUM/MINIMUM valid CLOSE of the 1st half (ties -> earliest date; value = anchor date close). 2nd anchors (high_2nd_over_period/low_2nd_over_period): the MAXIMUM/MINIMUM valid CLOSE found in [1st_anchor_position + gap, window_end] where gap = ceil(0.20*period) with a 20-trading-day floor for periods >= 60 (period 20 keeps the pure 20% gap) — the 2nd anchor is NOT confined to the 2nd half. The time-distance gap is enforced CONSTRUCTIVELY by shifting the 2nd anchor search range, guaranteeing |sec_pos - top_pos| >= gap wherever both exist. value = anchor date INTRADAY high/low. NaN closes are skipped; a half with no valid close NULLs its 1st anchor; when the 1st anchor is too close to the window end (top + gap > window_end), only the 2nd anchor is NULLed while the 1st stays valid. 2nd anchor dates are always strictly after the 1st anchor date. DATE columns record the anchor dates. change/days_since_last_high measure the move since the LAST high anchor (the later-dated of the two high anchors — the 2nd whenever present, else the 1st) and change/days_since_last_low the mirror on the low side; sign indicates growth or drop (negative = below that side''s anchor, positive = above it). sec_type ∈ {etf, index, stock}. Source: same DataFrame as mov_ave_spread parent pipeline (no second DB round-trip).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.sec_type     IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.code        IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.date        IS 'Business date (trading day).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.today_close IS 'Close price on `date` (COALESCE(adj_close, close) for ETFs; close for index/stock). NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.period      IS 'Rolling-window size in trading days: one of {20, 60, 120, 255, 500, 750, 1275}. Part of the PK — the long format stores one row per (code, sec_type, date, period).';

COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.open_over_period   IS 'Open price on the period-th trading day before `date`. NULL if fewer than `period` prior rows exist.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.high_over_period   IS 'Top-high anchor: the MAXIMUM valid CLOSE in the 1st HALF of the period window ([date-period+1, date] cut in half, h = L // 2; ties -> earliest date); value = the anchor date close. NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.high_date_over_period IS 'Business date of the top-high anchor (max valid CLOSE of the window''s 1st half). NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.low_over_period    IS 'Lowest-low anchor: the MINIMUM valid CLOSE in the 1st HALF of the period window (ties -> earliest date); value = the anchor date close. NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.low_date_over_period IS 'Business date of the lowest-low anchor (min valid CLOSE of the window''s 1st half). NULL when the half holds no valid close.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.high_2nd_over_period IS 'Second-high anchor: the MAXIMUM valid CLOSE found in [top_high_pos + gap, window_end] where gap = ceil(0.20*period) with a 20-trading-day floor for periods >= 60 (period 20 keeps the pure 20% gap) — the time-distance gap is enforced CONSTRUCTIVELY. NOT confined to the 2nd half; the search range starts from the gap AFTER the 1st anchor. value = the anchor date INTRADAY HIGH. NULL when the dynamic range holds no valid close OR when the 1st anchor is too close to the window end.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.high_2nd_date_over_period IS 'Business date of the second-high anchor. Always strictly after the top-high anchor date. NULL when the dynamic range holds no valid close OR when the 1st anchor is too close to the window end.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.low_2nd_over_period  IS 'Second-low anchor: the MINIMUM valid CLOSE found in [top_low_pos + gap, window_end] where gap = ceil(0.20*period) with a 20-trading-day floor for periods >= 60 (period 20 keeps the pure 20% gap) — the time-distance gap is enforced CONSTRUCTIVELY. NOT confined to the 2nd half. value = the anchor date INTRADAY LOW. NULL when the dynamic range holds no valid close OR when the 1st anchor is too close to the window end.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.low_2nd_date_over_period IS 'Business date of the second-low anchor. Always strictly after the lowest-low anchor date. NULL when the dynamic range holds no valid close OR when the 1st anchor is too close to the window end.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.high_line_slope_over_period IS 'Slope of the roof line through the two high anchors of this period (high_over_period close -> high_2nd_over_period intraday high), in price units per trading day: (high_2nd_over_period - high_over_period) / (trading days between the two anchor dates). Signed; the 2nd anchor always lies at least gap = ceil(0.20*period) days (20td floor for periods >= 60) after the top. NULL when either anchor is absent.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.low_line_slope_over_period  IS 'Slope of the floor line through the two low anchors of this period (low_over_period close -> low_2nd_over_period intraday low), in price units per trading day. Signed; the 2nd anchor always lies at least gap = ceil(0.20*period) days (20td floor for periods >= 60) after the bottom. NULL when either anchor is absent.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.change_since_last_high IS 'today_close MINUS the value of the LAST high anchor of this period — the LATER-dated of the two high anchors. The 2nd anchor date is always strictly after the 1st (the constructive gap), so the later-dated anchor is exactly the 2nd whenever it exists: high_2nd_over_period (value = anchor-date INTRADAY HIGH) when present, else high_over_period (value = anchor-date CLOSE). Sign indicates direction: negative = price sits below the roof anchor (dropped since the peak); positive = sits above it. NULL when the window has no high anchor, or when the winning anchor''s value is NULL (missing intraday high on the anchor date).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.days_since_last_high IS 'Trading days between `date` and the last high anchor date of this period (the same anchor change_since_last_high measures from — the 2nd high anchor whenever present, else the 1st). INTEGER >= 0. NULL only when the window has no high anchor (unlike change_since_last_high, this depends only on the anchor POSITION, so it is populated whenever the anchor exists even if its value is NULL).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.change_since_last_low IS 'today_close MINUS the value of the LAST low anchor of this period — the LATER-dated of the two low anchors, i.e. low_2nd_over_period (value = anchor-date INTRADAY LOW) whenever present, else low_over_period (value = anchor-date CLOSE); the 2nd anchor date is always strictly after the 1st. Sign indicates direction: positive = price sits above the floor anchor (grown since the trough); negative = sits below it. NULL when the window has no low anchor, or when the winning anchor''s value is NULL (missing intraday low on the anchor date).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_and_floor_lines.days_since_last_low IS 'Trading days between `date` and the last low anchor date of this period (the same anchor change_since_last_low measures from — the 2nd low anchor whenever present, else the 1st). INTEGER >= 0. NULL only when the window has no low anchor (unlike change_since_last_low, this depends only on the anchor POSITION, so it is populated whenever the anchor exists even if its value is NULL).';
