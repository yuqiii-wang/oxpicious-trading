-- ============================================================================
--  Margin Tech Stats — per-(sec_type, code, date) REGIME-DETECTION input for
--  the margin trend episode detection (analysis.margin_changes).
--
--  REDUCED SCHEMA: this table previously stored ma5/ma20/ma60 + slope + a
--  battery of slope-derivative columns for both margin_balance and
--  margin_buy. Only the two columns actually consumed by
--  analysis.margins.changes.detect_trend_episodes survive:
--    margin_balance_slope_ma5      — 5d smoothed balance slope (segmentation
--                                    signal: sign = UP/DOWN direction)
--    margin_balance_slope_zscore_20d — z-score of the daily slope vs its
--                                    20d window (significance filter)
--  Everything else (MAs, margin_buy slopes, slope_ma20/ma252/std20 raw
--  columns, CHECK constraints) was REMOVED in the margin cleanup.
--
--  Source tables:
--    stats.etf_liquidity_margin   — ETF margin balances (融资融券)
--    stats.stock_liquidity_margin — stock margin balances
--  'index' rows are the weighted-AVERAGE of constituent stocks' rz_balance
--  aggregated via the analysis.margin_index_series TABLE (aggregate-then-
--  compute; slope is a ratio / non-additive).
--
--  SCOPE — RONGZI ONLY (融资, cash borrow to buy). RONQIN (融券, sec
--  borrow) is INTENTIONALLY EXCLUDED per spec.
--
--  UNIVERSE FILTER (Python build script): only securities with at least
--  one non-zero rz_balance row in the last calendar month.
--
--  Table: analysis.margin_tech_stats
--    PK: (code, sec_type, date)
--
--  POPULATION: analysis.margins (Python, truncate-then-recompute). Per
--  project rule, ALL INSERTs are in Python — no raw INSERT...SELECT SQL.
--
--  Register in analysis.analysis_identity (name='margin_tech_stats').
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The smoothed slope and its significance on each security's margin balance curve — the trend-detection input for margin episode detection.
-- tags: margin, 融资融券, slope, 斜率, trend detection, 趋势检测

DROP TABLE IF EXISTS analysis.margin_tech_stats;

CREATE TABLE analysis.margin_tech_stats (
    sec_type        SMALLINT         NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- 5d rolling mean of the daily balance slope (sign = trend direction).
    margin_balance_slope_ma5  NUMERIC(18,6),

    -- (slope - slope_ma20) / slope_std20. Significance filter input.
    margin_balance_slope_zscore_20d NUMERIC(18,6),

    CONSTRAINT pk_margin_tech_stats PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'margin_tech_stats', 16);

COMMENT ON TABLE  analysis.margin_tech_stats IS 'Per-(code, sec_type, date) regime-detection input for analysis.margin_changes trend episode detection. REDUCED to the two columns consumed by detect_trend_episodes: margin_balance_slope_ma5 (5d smoothed rz_balance slope; sign = UP/DOWN segmentation signal) and margin_balance_slope_zscore_20d (slope z-score vs 20d window; magnitude significance filter). SECTIONED (2026-10-19): the curve is cut at null-balance gaps (first valid row after a 0/NULL run) and at contributing-membership changes on aggregate curves (industry member count, index n_with_balance) — the daily slope is NULL on a boundary row and the MA5/MA20/STD20 windows never mix slopes from different sections, so no rise/drop is attributed across a boundary. sec_type ∈ {etf, stock, index, industry} — ''index'' rows aggregated from the analysis.margin_index_series TABLE, ''industry'' rows from the margin_industry_stats SUM(stock+etf) aggregate (code = industry_id). RONGZI (融资) only. Built by analysis.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_tech_stats.sec_type IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity), 3 = industry (the industry SUM aggregate; code = industry_id).';
COMMENT ON COLUMN analysis.margin_tech_stats.code IS 'Security ticker: ETF bare e.g. "159001"; stock with exchange suffix e.g. "600008.SS".';
COMMENT ON COLUMN analysis.margin_tech_stats.date IS 'Trading date.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope_ma5 IS '5-trading-day rolling mean of the daily rz_balance slope ((X[t]-X[t-1])/X[t-1]) per (sec_type, code), computed WITHIN sections (null-gap / membership-change boundaries): the slope is NULL on a boundary row and the window never spans one. Sign = direction of the smoothed balance move — the segmentation signal for margin_changes trend episodes.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope_zscore_20d IS '(slope - slope_ma20) / slope_std20 per (sec_type, code) — all three within the same section. NULL when the rolling std is NaN or <= 0. Magnitude-only significance filter: a margin_changes trend is kept when a MAJORITY of its days have |zscore| > 0.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_tech_stats', 'margin_tech_stats', NULL, NOW(),
     'Per-(sec_type, code, date) regime-detection input for analysis.margin_changes trend detection: margin_balance_slope_ma5 (segmentation signal) + margin_balance_slope_zscore_20d (significance filter). sec_type ∈ {etf, stock, index, industry} (''index'' aggregated from analysis.margin_index_series TABLE; ''industry'' from the margin_industry_stats SUM aggregate — code = industry_id). RONGZI only. Built by analysis.margins (truncate-then-recompute); all INSERTs in Python.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
