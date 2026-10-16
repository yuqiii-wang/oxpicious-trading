-- ============================================================================
--  analysis.trends — the consolidated TRENDS + EPISODES component's SQL
--  (01_trend_days.sql). Independent component: the trend/episode
--  primitives live in the analysis.trends python package and their
--  stores here — consumers (analysis.mov_ave_spread,
--  analysis.analysis_signals, the data_viz API) import/join THESE,
--  never the other way round. analysis.* never imports
--  analysis.analysis_signals.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.trend_days  (per-(code, date) price TREND state)
--
--  MOVED 2026-10-15 from analysis_signals.code_trend_days (retired by
--  this file) — same shape, new owner: the trends component. One row
--  per (code, date) carrying the code's own TREND classification:
--  the price-structure state (top / bottom / rise / fall / unknown)
--  computed by analysis.trends.trend_state.attach_trend from the
--  code's OWN history (top/bottom = the price-vs-MA255 gap beyond its
--  own trailing-255d q90/q10 with a matching MA20 slope; rise/fall =
--  the Wilder-RSI10 RE-ENTRY episodes vs the fixed 50±12.5 momentum
--  zones, alive through <= 5 out-of-band traded rows + MA20-slope
--  agreement, with the DEEP-ANCHOR TURN (a deep RSI <=30/>=70 touch
--  opens a 40-traded-row window in which the midline-50 re-cross with
--  slope flips the trend — the post-crash rebound fix) — the
--  2026-10-15 replacement of the delayed price-vs-MA60 states;
--  unknown = neither episode; trailing quantiles shifted one row — no
--  look-ahead; identical inputs on every family's prepared frame, so
--  ONE store serves them all).
--
--  Written by python -m analysis.analysis_signals (the first family's
--  prepared frame of each run rewrites the run's (sec_type, codes)
--  scope — force-purge + copy, idempotent). Read by:
--    - the trend-spans API (GET /api/live-data/trading-signals/
--      trend-spans) → the code-trend chart's TREND band shading
--    - the live tier's cell matching (live.live_signals — the day's
--      trend picks which (trend, regime) rule cell may fire)
--
--  LIVE-DB MIGRATION: the retirement DROP below removes the legacy
--  analysis_signals table; against a live DB, copy the rows over
--  first (temp_scripts/_apply_trends_consolidation.py does
--  INSERT ... SELECT before the drop).
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis_signals.code_trend_days CASCADE;

CREATE TABLE analysis.trend_days (
    code            TEXT     NOT NULL,  -- ticker (etf "510050" / index "000300" / stock)
    sec_type        SMALLINT NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    date            DATE     NOT NULL,  -- the trading day (the trend state at that day's close)

    trend           SMALLINT NOT NULL,  -- label code (static.label_dim 'trend'): 0 top (gap255 > own trailing q90, rising) | 1 bottom (gap255 < own q10, falling) | 2 rise (RSI14 re-entry up-episode alive, incl. the deep-anchor turn window) | 3 fall (symmetric) | 5 unknown (neither episode / warmup) — 'all' 4 is the rules-tier POOLED stamp only, never a day state

    PRIMARY KEY (code, sec_type, date)
);

COMMENT ON TABLE  analysis.trend_days IS 'Per-(code, date) TREND classification (analysis.trends.trend_state.attach_trend: top/bottom the overextended extremes of the price-vs-MA255 gap vs its own trailing q90/q10 with matching MA20 slope; rise/fall the Wilder-RSI14 RE-ENTRY episodes vs the fixed 50±7.5 momentum zones with <= 5-row re-entry absorb + the EFFICIENCY GUARD (the Kaufman 10-row net-change sign — the 2026-10-15 online-candidates study's measured replacement of the MA20-slope guard) + the DEEP-ANCHOR TURN (a deep RSI <=30/>=70 touch opens a 40-row window where the midline-50 re-cross with the guard flips the trend — the post-crash rebound fix) — the 2026-10-15 replacement of the delayed price-vs-MA60 states; else unknown — trailing quantiles shifted one row, no look-ahead). Owned by the consolidated analysis.trends component (moved 2026-10-15 from analysis_signals.code_trend_days). Written by the analysis_signals pipeline (first family run of each invocation rewrites the scope); read by the trend-spans API (chart trend shading) and the live tier (day-context rule-cell matching).';
COMMENT ON COLUMN analysis.trend_days.trend IS 'static.label_dim trend code: 0 top | 1 bottom | 2 rise | 3 fall | 5 unknown (the label_dim 4 ''all'' is the rules-tier POOLED stamp only, never written here)';
