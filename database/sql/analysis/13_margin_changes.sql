-- ============================================================================
--  Margin Changes — per-(sec_type, code, trend) summary of SIGNIFICANT margin
--  balance TRENDS (sustained UP or DOWN moves) on the RONGZI (融资 /
--  cash-borrow) margin balance curve.
--
--  REDUCED SCHEMA: the metric columns are trimmed to the two most important:
--    new_buy                    — rz_buy (融资买入额) on the episode's
--                                 end_date (yuan, FLOW). The latest day's
--                                 fresh rongzi buy amount.
--    rz_buy_vs_trading_amt_ratio — Σ rz_buy / Σ trading_amount over
--                                 [start_date, end_date]. Fraction of total
--                                 market turnover from rongzi buys.
--  REMOVED in the margin cleanup: netting_buy, rsi_trend, the 4 OHLC
--  margin-balance columns, ratio_rsi_margin_vs_price, the 4 OHLC
--  margin/price ratio columns, and CHECK constraints.
--
--  PURPOSE
--    A "margin change" row is a single TREND EPISODE on the rz_balance
--    series: a contiguous span [start_date, end_date] during which the
--    rongzi outstanding balance moved persistently in one direction
--    (UP = balance accumulating / traders adding leveraged longs, or
--    DOWN = balance unwinding / traders cutting leverage).
--
--  SCOPE — RONGZI ONLY (融资, cash borrow to buy). RONQIN (融券, sec
--  borrow) is INTENTIONALLY EXCLUDED per spec.
--
--  TREND DETECTION (in Python — analyze.margins.changes.detection)
--    Segmentation signal: margin_balance_slope_ma5 sign
--    (slope_ma5 > 0 = UP, slope_ma5 < 0 = DN). GAP BRIDGING: short
--    opposite-direction runs of <= 3 days between two same-direction runs
--    are absorbed. SIGNIFICANCE FILTER: a MAJORITY (>50%) of a trend's
--    days must have |zscore_20d| > 0. Min trend length: 3 trading days.
--
--  Table: analysis.margin_changes
--    PK: (code, sec_type, start_date, end_date)
--    sec_type ∈ ('etf' | 'stock' | 'index')  — 'index' rows aggregated
--    from the margin_index_series TABLE.
--
--  POPULATION
--    analyze.margins (Python module). Per project rule, ALL INSERTs are
--    in Python — no raw INSERT...SELECT SQL in this file. Truncate-then-
--    recompute on every run (episode boundaries shift when new dates
--    arrive).
--
--  Register in analysis.analysis_identity (name='margin_changes').
-- ============================================================================

DROP TABLE IF EXISTS analysis.margin_changes;

CREATE TABLE analysis.margin_changes (
    code                       TEXT          NOT NULL,
    sec_type                   TEXT          NOT NULL,  -- 'etf' | 'stock' | 'index'

    start_date                 DATE          NOT NULL,
    end_date                   DATE          NOT NULL,
    days_of_trend              INTEGER       NOT NULL,

    is_trend_up_not_down       BOOLEAN       NOT NULL,

    -- rz_buy (融资买入额) on the episode end_date (yuan, FLOW).
    new_buy                    NUMERIC(24,4),

    -- Σ rz_buy / Σ trading_amount over [start_date, end_date].
    rz_buy_vs_trading_amt_ratio NUMERIC(14,4),

    CONSTRAINT pk_margin_changes PRIMARY KEY (code, sec_type, start_date, end_date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'margin_changes', 8);

COMMENT ON TABLE  analysis.margin_changes IS 'Per-(sec_type, code, trend) summary of SIGNIFICANT margin balance TRENDS (sustained UP or DOWN moves) on the RONGZI (融资) margin balance curve. One row per trend episode: [start_date, end_date] span with direction (is_trend_up_not_down), span length (days_of_trend), new_buy (rz_buy on end_date, yuan FLOW), and rz_buy_vs_trading_amt_ratio (Σ rz_buy / Σ trading_amount — fraction of turnover from rongzi buys; plotted on the Margin Trends Buy chart). sec_type ∈ {etf, stock, index} — ''index'' rows aggregated from the margin_index_series TABLE. RONQIN (融券 / sec borrow) EXCLUDED. Trend detection: contiguous run of same-sign 5-day smoothed balance slope (margin_balance_slope_ma5 > 0 = UP, < 0 = DOWN), min 3 days, gap bridging <= 3 days, majority zscore significance. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_changes.code                  IS 'Security ticker with exchange suffix, e.g. "159001.SZ" (ETF) or "600008.SS" (stock). For sec_type=''index'': bare 6-digit index code (e.g. 000300) whose margin series is aggregated from the margin_index_series TABLE.';
COMMENT ON COLUMN analysis.margin_changes.sec_type             IS 'Subject security type: etf, stock, or index.';
COMMENT ON COLUMN analysis.margin_changes.start_date           IS 'Trend episode start date (inclusive). First date of the sustained UP or DOWN move on the rz_balance series.';
COMMENT ON COLUMN analysis.margin_changes.end_date             IS 'Trend episode end date (inclusive). Last date of the sustained move.';
COMMENT ON COLUMN analysis.margin_changes.days_of_trend        IS 'Span length in TRADING days (inclusive of both endpoints).';
COMMENT ON COLUMN analysis.margin_changes.is_trend_up_not_down IS 'Trend direction. TRUE = balance ACCUMULATING (traders adding leveraged longs — rz_balance rising). FALSE = balance UNWINDING (traders cutting leverage — rz_balance falling).';
COMMENT ON COLUMN analysis.margin_changes.new_buy              IS 'rz_buy (融资买入额) on the trend end_date — the episode''s last day of fresh rongzi BUY amount (yuan, FLOW). NULL when the end-date rz_buy is 0 / unavailable (no rongzi buy flow that day).';
COMMENT ON COLUMN analysis.margin_changes.rz_buy_vs_trading_amt_ratio IS 'Σ rz_buy over [start_date, end_date] / Σ trading_amount over the same window. Fraction of total market turnover represented by rongzi (融资) buy activity. NULL when trading_amount is unavailable or 0. NUMERIC(14,4).';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_changes', 'margin_changes', NULL, NOW(),
     'Per-(sec_type, code, trend) summary of SIGNIFICANT margin balance TRENDS on the RONGZI (融资) margin balance curve. One row per trend episode: [start_date, end_date] span with direction (is_trend_up_not_down), span length (days_of_trend), new_buy (rz_buy on end_date, yuan FLOW), and rz_buy_vs_trading_amt_ratio (Σ rz_buy / Σ trading_amount — fraction of turnover from rongzi buys). sec_type ∈ {etf, stock, index} — ''index'' rows aggregated from margin_index_series TABLE. RONQIN (融券) EXCLUDED. Trend detection: contiguous run of same-sign 5-day smoothed balance slope, min 3 days, gap bridging <= 3 days, majority zscore significance. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

