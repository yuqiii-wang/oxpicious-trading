-- ============================================================================
--  mov_ave_spreads suite — QUANTITATIVE ROOF/FLOOR LEVEL ESTIMATES.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql; the anchor
--  semantics feeding the trendline method: see
--  03_mov_ave_spreads_roof_and_floor_lines.sql.
-- ============================================================================

-- Drop any prior version so a re-run rebuilds the table from the CREATE
-- below; also remove the stale analysis_identity row so the registration
-- starts clean. The table is derived data — fully recomputed by the next
-- analysis.mov_ave_spread run (the incremental missing-date check sees
-- every date missing after the DROP).
DROP TABLE IF EXISTS analysis.mov_ave_spreads_roof_floor_estimates CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_spread_roof_floor_estimates';

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_spreads_roof_floor_estimates  (LONG format)
--    One row per (sec_type, code, date, period, method): the ESTIMATED
--    roof (resistance) and floor (support) price LEVEL AT `date` under one
--    quantitative method, computed from the rolling window of `period`
--    trading days ENDING at `date` (strictly causal — no lookahead past
--    `date`). period ∈ {60, 120, 255, 500, 750, 1275} (the roof/floor
--    windows; the 20d window is not a roof/floor window). method is the
--    static.label_dim 'roof_floor_method' SMALLINT code:
--
--      0 trendline  — the roof/floor LINE through the window's (top, 2nd)
--                     anchors (03_...roof_and_floor_lines.sql semantics),
--                     EXTRAPOLATED to `date`:
--                     roof = high_2nd + high_line_slope × days_since_last_high
--                     (the 2nd anchor value stepped forward by the line's
--                     slope; NULL whenever the anchors do not determine a
--                     line). The repo's own structural method.
--      1 donchian   — Donchian channel bounds: the MAXIMUM intraday high /
--                     MINIMUM intraday low of the window (the hard channel
--                     edge; classic breakout system levels).
--      2 quantile   — rolling close-quantile band: the 90th / 10th
--                     percentile of valid closes in the window (a
--                     distributional envelope — where price spent the
--                     window's upper/lower deciles).
--      3 channel    — linear-regression channel: OLS of valid closes on
--                     trading-day position over the window, evaluated at
--                     `date`, ± 2 × residual σ (a sloped Raff-style
--                     channel; needs ≥ 3 valid closes).
--      4 boll       — Bollinger-style band at the window: SMA(close,
--                     period) ± 2 × population σ (the flat band around
--                     the window mean).
--      5 median     — the per-(date, period) MEDIAN of methods 0..4's
--                     roof / floor values (NaN-skipping; the robust
--                     cross-method consensus row — NULL when every method
--                     is NULL).
--
--  All methods use the same window region rule as the anchors:
--  [max(0, i-period+1), i] in trading-day positions (partial windows at a
--  code's history start; NaN closes skipped).
--
--  Columns:
--    sec_type, code, date, period, method  — PK (method = its
--                         static.label_dim 'roof_floor_method' SMALLINT
--                         code; label decode joins static.label_dim on
--                         (domain, code))
--    today_close           — close price on `date` (the estimate reference)
--    roof_estimate         — the estimated RESISTANCE level at `date`
--    floor_estimate        — the estimated SUPPORT level at `date`
--
--  Populated by the internal estimate step of `analysis.mov_ave_spread`
--  (roof_floor_estimate step package). Reuses the same source DataFrame
--  as the parent pipeline — no second DB round-trip.
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.mov_ave_spreads_roof_floor_estimates (
    sec_type          SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code              TEXT         NOT NULL,
    date              DATE         NOT NULL,
    period            INTEGER      NOT NULL,  -- window size in trading days: {60,120,255,500,750,1275}
    method            SMALLINT     NOT NULL,  -- label code (static.label_dim 'roof_floor_method')

    today_close       NUMERIC(18,6) NOT NULL,
    roof_estimate     NUMERIC(18,6),
    floor_estimate    NUMERIC(18,6),

    CONSTRAINT pk_mov_ave_spreads_roof_floor_estimates
        PRIMARY KEY (code, sec_type, date, period, method)
) PARTITION BY HASH (code);

-- Native hash partitions (32) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('analysis', 'mov_ave_spreads_roof_floor_estimates', 32);

COMMENT ON TABLE  analysis.mov_ave_spreads_roof_floor_estimates IS 'Quantitative roof/floor level estimates (LONG format): one row per (sec_type, code, date, period, method) with the estimated resistance (roof_estimate) / support (floor_estimate) price level AT `date` under one quantitative method, computed from the strictly-causal rolling window of `period` ∈ {60,120,255,500,750,1275} trading days ending at `date`. method is its static.label_dim (''roof_floor_method'') SMALLINT code: 0 trendline (the roof/floor anchor LINE of mov_ave_spreads_roof_and_floor_lines extrapolated to `date`: high_2nd + high_line_slope × days_since_last_high on each side), 1 donchian (window max intraday high / min intraday low), 2 quantile (rolling 90th/10th percentile of valid closes), 3 channel (OLS close-on-position regression fit at `date` ± 2 residual σ, ≥3 valid closes), 4 boll (SMA(period) ± 2 population σ), 5 median (the NaN-skipping median of methods 0-4 per (date, period) — the cross-method consensus). Same window region rule as the anchors: [max(0, i-period+1), i] in trading-day positions; partial windows at history start; NaN closes skipped. Source: same DataFrame as the mov_ave_spread parent pipeline (no second DB round-trip). sec_type ∈ {etf, index, stock}.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_estimates.sec_type IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_estimates.code IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_estimates.date IS 'Business date (trading day) the estimate applies AT — every method reads only the window ending at this date (strictly causal).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_estimates.period IS 'Rolling-window size in trading days: one of {60, 120, 255, 500, 750, 1275}. Part of the PK — the long format stores one row per (code, sec_type, date, period, method).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_estimates.method IS 'The quantitative estimate method as its static.label_dim (''roof_floor_method'') SMALLINT code: 0 = trendline (anchor-line extrapolation), 1 = donchian (window extremes), 2 = quantile (close quantile band), 3 = channel (linear-regression channel), 4 = boll (Bollinger-style band at the window), 5 = median (cross-method consensus).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_estimates.today_close IS 'Close price on `date` (COALESCE(adj_close, close) for ETFs; close for index/stock). NOT NULL.';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_estimates.roof_estimate IS 'The estimated RESISTANCE (roof) price level at `date` under this row''s method — see the method codes in the table comment. NULL when the method cannot determine a level for this window (e.g. trendline with no 2nd anchor, channel with < 3 valid closes).';
COMMENT ON COLUMN analysis.mov_ave_spreads_roof_floor_estimates.floor_estimate IS 'The estimated SUPPORT (floor) price level at `date` under this row''s method — see the method codes in the table comment. NULL when the method cannot determine a level for this window.';
