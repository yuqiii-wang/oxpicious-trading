-- ============================================================================
--  Table: stats.mov_ave_market_hypes  (market-hype EPISODE detector)
--    One row per (sec_type, code, min_checkin_period, EPISODE): a
--    CONCATENATED hype episode — a maximal span of trading dates
--    anchored on a maximal run of CONSECUTIVE "hyped" dates (trading
--    amount AND price volatility BOTH elevated, SUSTAINEDLY over a
--    check-in window, each measured against its own CENTERED 20-year
--    percentile threshold) and EXTENDED through the surrounding
--    check-in evidence, bucketed BY ITS LENGTH: min_checkin_period is
--    the bucket's MINIMUM span and the NEXT window its EXCLUSIVE
--    maximum (5d: 5..19 rows; 20d: 20..59; 60d: 60..119; 120d:
--    120..254; 255d: 255..5100 = the whole ±10y threshold base).
--
--  Computation semantics:
--
--  1. CENTERED PERCENTILE THRESHOLDS (per date t, per code — the audit
--     base window spans BOTH directions around the audited date, NOT a
--     trailing/rolling-back window):
--     trading_amt_threshold[t] = the min_trading_amt_threshold-th
--         PERCENTILE (0-100, linear interpolation) of daily
--         trading_amount over the centered window of ~2550 trading
--         rows (10 trading years) BEFORE t, t itself, and ~2550 rows
--         AFTER t (~5101 rows ≈ 20 trading years total).
--     std_threshold[t] = the min_std_threshold-th percentile of
--         std_{W}days over the same centered window, where W = the
--         row's min_checkin_period (matching timescale: the
--         volatility metric is the W-day rolling population σ of
--         price, computed by the build the same way the mov_ave_spread
--         parent pipeline computes std_{W}days).
--     A base window with fewer than 255 non-NULL observations (1
--     trading year) has no thresholds -> the date is not hyped. Bases
--     near the start / end of a code's history are naturally truncated
--     (the newest dates have no future rows yet, so their base is
--     effectively the trailing 10y). Because the base looks both ways,
--     historical rows use their FOLLOWING decade (retrospective audit,
--     look-ahead by design) — run --force to refresh historical rows'
--     flags after new data arrives.
--
--  2. CHECK-IN CONDITION (per date s):
--     checkin[s] = trading_amount[s] > trading_amt_threshold[s]
--                  AND std_{W}days[s] > std_threshold[s]
--     Strict > on both legs. NULL turnover / σ counts as NOT a
--     check-in.
--
--  3. SATISFACTION (per row date t, "within min_checkin_period from
--     today date"):
--     Within the last W trading rows ending at t (inclusive), the
--     percentage of check-in dates must EXCEED
--     min_checkin_satisfaction_threshold (strict >; the 60.0 default
--     means "> 60% of the W days checked in"). The denominator is the
--     full W rows — missing data counts against satisfaction. The
--     first W-1 rows of each code have no full window -> not hyped.
--
--  4. EPISODE CONCAT + EXTENSION + BUCKETING (what the table stores):
--     The per-date hyped series from (3) is collapsed, per (sec_type,
--     code, min_checkin_period), into maximal runs of CONSECUTIVE
--     hyped dates ("cores"), then each core is extended through the
--     check-in evidence that fed its satisfaction and bucketed by its
--     SPAN:
--       - start: the FIRST check-in within the W rows ending at the
--         core's first hyped date (the lookback window that produced
--         the core's first satisfaction verdict — its earliest
--         evidence). This lets an episode start at the FIRST big-move
--         day of a turmoil instead of ~W rows later, when the trailing
--         satisfaction count finally crosses the threshold (the
--         2024-09-24 rally audit, 159673.SZ: the 20d satisfaction only
--         crossed 60% on 2024-10-21 — a full month late — while the
--         check-ins began on the rally's day 1).
--       - end: symmetric — the LAST check-in within the W rows
--         starting at the core's last hyped date (the decaying tail).
--       - episodes of one bucket never overlap; each start is clipped
--         to just after the previous episode's end.
--       - BUCKET BOUNDS: hype_days (the span in trading dates,
--         start and end inclusive) must satisfy
--         W <= hype_days < next check-in window (the longest window
--         is bounded by 5100 rows = the whole ±10y base). A core whose
--         own consecutive span already reaches its bucket max is
--         dropped from that bucket: sustained activity of that length
--         is the domain of the NEXT bucket up, whose own
--         longer-window satisfaction flags it.
--       - trading_amt_hype_days / std_hype_days count the days within
--         the stored span on which each leg individually checked in
--         (diagnostics for which leg drove the episode).
--     Only qualifying spans are stored — non-hyped dates leave no
--     footprint.
--
--  Row multiplicity: one row per EPISODE per check-in window
--  (5 / 20 / 60 / 120 / 255). min_checkin_period IS part of the PK —
--  different windows can produce episodes with identical spans, so
--  the window must disambiguate the rows. The three threshold columns
--  RECORD the parameter set the build used (defaults 60.0 / 60.0 /
--  30.0 — the σ leg sits at a DELIBERATELY LOW 30th percentile: the
--  W-day trailing σ lags a sudden turmoil by construction, so a low
--  bar lets episodes start at the turmoil's first big-move day); they
--  are NOT part of the PK — rebuilding with different parameters (use
--  --force) overwrites in place.
--
--  Rebuild semantics (margin_changes precedent): episode boundaries
--  shift whenever new dates arrive (the trailing episode extends; the
--  centered threshold windows move), and non-hyped dates leave no
--  footprint — date-level coverage cannot be diffed against an
--  episodes table. The build therefore DELETEs its entire scope (one
--  sec_type, or one code in --code mode) and recomputes every episode
--  from the FULL per-code history on every run; --force additionally
--  truncates the table first.
--
--  POPULATION: `python -m builds.market_hypes` (builds/market_hypes —
--  ETF + Index + Stock in one table, sec_type discriminates; the
--  source price / trading_amount columns come from the same stats
--  tables the analyze.mov_ave_spread pipeline reads). MIGRATED from
--  analysis.mov_ave_market_hypes (formerly an internal step of
--  analyze.mov_ave_spread) — the analysis-schema DDL in
--  database/sql/analysis/03_mov_ave_spreads.sql now only DROPs the
--  old table.
-- ============================================================================

DROP TABLE IF EXISTS stats.mov_ave_market_hypes CASCADE;

CREATE TABLE stats.mov_ave_market_hypes (
    sec_type                        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code                            TEXT         NOT NULL,
    start_date                      DATE         NOT NULL,
    end_date                        DATE         NOT NULL,
    min_checkin_period              INTEGER      NOT NULL,  -- 5 | 20 | 60 | 120 | 255
    hype_days                       INTEGER      NOT NULL,

    min_checkin_satisfaction_threshold NUMERIC(6,4) NOT NULL DEFAULT 60.0,
    min_trading_amt_threshold          NUMERIC(6,4) NOT NULL DEFAULT 60.0,
    trading_amt_hype_days              INTEGER      NOT NULL,
    min_std_threshold                  NUMERIC(6,4) NOT NULL DEFAULT 30.0,
    std_hype_days                      INTEGER      NOT NULL,

    CONSTRAINT pk_mov_ave_market_hypes PRIMARY KEY (code, sec_type, start_date, end_date, min_checkin_period)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'mov_ave_market_hypes', 8);

COMMENT ON TABLE  stats.mov_ave_market_hypes              IS 'Market-hype EPISODE detector: one row per (code, sec_type, min_checkin_period, episode) — a CONCATENATED hype episode: a maximal span of trading dates anchored on a maximal run of consecutive hyped dates and extended through the surrounding check-in evidence (the W rows before the run''s first hyped date, back to its first check-in, and the W rows after the last hyped date, to its last check-in). A date is hyped when, within the last min_checkin_period (W) trading rows ending at it, MORE than min_checkin_satisfaction_threshold percent of the dates are check-ins — a check-in being a date whose daily trading_amount exceeds its centered-20-year min_trading_amt_threshold percentile AND whose W-day rolling population σ (std_{W}days) exceeds its centered-20-year min_std_threshold percentile (strict > on both legs; matching timescale: the σ window equals the check-in window). The audit base window is CENTERED on each audited date — ~2550 trading rows (10 trading years) before the date plus ~2550 rows after it (NOT a trailing/rolling-back window); windows with < 255 observations have no thresholds -> the date is not hyped; bases near the start/end of a code''s history are naturally truncated (newest dates have no future rows yet). Episodes are BUCKETED BY SPAN: min_checkin_period is the bucket minimum, the next window the exclusive maximum (20d: 20..59 rows; 60d: 60..119; 120d: 120..254; 255d: 255..5100 = the whole ±10y base) — one calendar turmoil lands in exactly the bucket matching its length. Non-hyped dates leave no footprint; episodes are REBUILT WHOLESALE per sec_type on every run of builds.market_hypes (new dates shift episode boundaries — the margin_changes precedent). sec_type ∈ {etf, index, stock}; one episode set per check-in window (5/20/60/120/255).';
COMMENT ON COLUMN stats.mov_ave_market_hypes.sec_type   IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN stats.mov_ave_market_hypes.code         IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN stats.mov_ave_market_hypes.start_date   IS 'Episode start date (inclusive): the earliest date of the episode''s CONCATENATED span — the FIRST check-in within the W-row lookback evidence window ending at the core run''s first hyped date (so episodes start at a turmoil''s first big-move day, not ~W rows later when the trailing satisfaction count finally crosses the threshold).';
COMMENT ON COLUMN stats.mov_ave_market_hypes.end_date     IS 'Episode end date (inclusive): the latest date of the episode''s CONCATENATED span — the LAST check-in within the W-row lookforward window starting at the core run''s last hyped date (the decaying tail). A code''s trailing episode extends as new hyped dates arrive — the build deletes + recomputes its whole scope, so stored boundaries are always wholesale-rebuilt. >= start_date.';
COMMENT ON COLUMN stats.mov_ave_market_hypes.min_checkin_period                 IS 'Check-in window length W in trading rows: the count of check-in dates is taken over the last W rows ending at each audited date (inclusive), and W doubles as the bucket''s MINIMUM episode span (the next window is the exclusive maximum; the 255d bucket is bounded by the whole ±10y base instead). Allowed values 5 / 20 / 60 / 120 / 255 — one episode set per window; the volatility leg uses the matching-timescale σ (std_{W}days). Part of the PK (two windows can produce episodes with identical spans).';
COMMENT ON COLUMN stats.mov_ave_market_hypes.hype_days    IS 'Episode SPAN in TRADING dates: the number of trading rows from start_date to end_date inclusive — the CONCATENATED length (check-in days PLUS bridged interior gaps), bucket-filtered to [min_checkin_period, next check-in window) so each calendar turmoil lands in exactly the bucket matching its length.';
COMMENT ON COLUMN stats.mov_ave_market_hypes.min_checkin_satisfaction_threshold IS 'Required percentage (0-100] of check-in dates within the window, strict greater-than. Default 60.0 = more than 60% of the W dates must be check-ins. Recorded build parameter — NOT part of the PK; changing it requires a --force rebuild.';
COMMENT ON COLUMN stats.mov_ave_market_hypes.min_trading_amt_threshold          IS 'Centered-20-year PERCENTILE level (0-100, linear interpolation) of daily trading_amount used as the liquidity-leg threshold: a date checks in on this leg when its trading_amount exceeds the percentile of its centered window (~2550 rows before the date + ~2550 rows after it, ±10 trading years). Default 60.0 (60th percentile). Recorded build parameter — NOT part of the PK; changing it requires a --force rebuild.';
COMMENT ON COLUMN stats.mov_ave_market_hypes.trading_amt_hype_days              IS 'Days within the episode span (start_date..end_date inclusive) on which the LIQUIDITY leg individually checked in (trading_amount > its centered-20y percentile). Diagnostic for which leg drove the episode; <= hype_days (interior bridged gaps did not check in).';
COMMENT ON COLUMN stats.mov_ave_market_hypes.min_std_threshold                  IS 'Centered-20-year PERCENTILE level (0-100) of std_{W}days used as the volatility-leg threshold: a date checks in on this leg when its W-day rolling population σ exceeds the percentile of its centered window (~2550 rows before the date + ~2550 rows after it, ±10 trading years). Default 30.0 (30th percentile) — DELIBERATELY LOW: the W-day trailing σ lags a sudden turmoil by construction (the window still holds W-1 pre-turmoil rows on day 1), so a low bar lets episodes start at the turmoil''s first big-move day (the 2024-09-24 rally audit showed a 60th-pct σ leg delayed episode starts by a full month while the amt leg fired from day one). Recorded build parameter — NOT part of the PK; changing it requires a --force rebuild.';
COMMENT ON COLUMN stats.mov_ave_market_hypes.std_hype_days                      IS 'Days within the episode span (start_date..end_date inclusive) on which the VOLATILITY leg individually checked in (std_{W}days > its centered-20y percentile). Diagnostic for which leg drove the episode; <= hype_days (interior bridged gaps did not check in).';
