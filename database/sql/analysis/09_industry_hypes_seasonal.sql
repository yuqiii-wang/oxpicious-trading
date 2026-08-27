-- ============================================================================
--  Industry Hypes & Drains — SEASONAL (monthly) ranking.
--
--  Companion to analysis.industry_hypes_and_drains (which stores PER-DATE
--  rankings). This table stores PER-MONTH rankings: for each calendar month,
--  the top-5 HYPE + bottom-5 DRAIN industries ranked by their PEAK attribution
--  contribution within the month.
--
--  Table: analysis.industry_hypes_seasonal
--    PK: (season_qkey, benchmark_code, period_days, rank_side, rank)
--
--  PURPOSE
--    The Market Trend "Hypes & Drains" chart uses SEASONAL rankings to
--    determine which industry curves to show. The plot is still daily
--    (benchmark + industry rolling curves are daily), but WHICH 10 industries
--    appear is frozen per month. Industries that drop out of the top/bottom
--    5 in a later month — but whose curve is still on the SAME side of the
--    benchmark — are shown as faded (very light transparent) curves. Curves
--    that CROSS the benchmark disappear. If an industry returns to the
--    top/bottom 5 in a future month, it reappears at full opacity.
--
--  RANKING METHOD
--    For each (month, benchmark, period, weighting, industry):
--      HYPE:  peak_metric_value = MAX(metric_value) over all trading days
--             in the month. Ranked DESC → top 5.
--      DRAIN: peak_metric_value = MIN(metric_value) over all trading days
--             in the month. Ranked ASC → bottom 5 (most negative first).
--    metric_value is hype (equal weighting) or hype × shared_trading_amt
--    (amt weighting). Captures the strongest moment of each industry
--    within the month.
--
--  SEASON KEY
--    season_qkey = 'YYYY-MM' (e.g. '2026-08').
--    season_start / season_end are the calendar boundaries of the month.
--
--  SOURCE
--    analysis.industry_hypes_and_drains (per-date rankings, which in turn
--    derive from analysis.industry_attributions).
--
--  POPULATION
--    analyze.industry_sentiments.hypes_and_drains (internal step, after the
--    per-date table is populated). Truncate-then-recompute.
-- ============================================================================
DROP TABLE IF EXISTS analysis.industry_hypes_seasonal;

CREATE TABLE IF NOT EXISTS analysis.industry_hypes_seasonal (
    season_qkey       TEXT          NOT NULL,  -- '2026-08'
    season_year       INTEGER       NOT NULL,  -- 2026
    season_month      SMALLINT      NOT NULL,  -- 1..12
    season_start      DATE          NOT NULL,  -- calendar month start
    season_end        DATE          NOT NULL,  -- calendar month end
    benchmark_code    TEXT          NOT NULL,
    period_days       INTEGER       NOT NULL,  -- 5 | 20 | 60 | 120 | 255 | 500
    weighting         TEXT          NOT NULL DEFAULT 'equal',  -- 'equal' | 'amt'
    rank_side         TEXT          NOT NULL,  -- 'HYPE' | 'DRAIN'
    rank              SMALLINT      NOT NULL,  -- 1 | 2 | 3 | 4 | 5
    industry_id       TEXT          NOT NULL,
    industry_label    TEXT          NOT NULL DEFAULT '',
    -- Peak hype within the month.
    -- HYPE: MAX(metric_value) over all trading days in the month.
    -- DRAIN: MIN(metric_value) over all trading days in the month.
    -- For weighting='equal': peak of hype.
    -- For weighting='amt': peak of hype × shared_trading_amt.
    peak_metric_value NUMERIC(24,6),

    CONSTRAINT pk_industry_hypes_seasonal PRIMARY KEY
        (benchmark_code, season_qkey, period_days, weighting, rank_side, rank),
    CONSTRAINT chk_seasonal_period_days  CHECK (period_days IN (5, 20, 60, 120, 255, 500)),
    CONSTRAINT chk_seasonal_weighting    CHECK (weighting IN ('equal', 'amt')),
    CONSTRAINT chk_seasonal_rank_side    CHECK (rank_side IN ('HYPE', 'DRAIN')),
    CONSTRAINT chk_seasonal_rank         CHECK (rank BETWEEN 1 AND 5),
    CONSTRAINT chk_seasonal_month        CHECK (season_month BETWEEN 1 AND 12)
) PARTITION BY HASH (benchmark_code);

-- Native hash partitions (8) keyed by benchmark_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'industry_hypes_seasonal', 8);

-- idx_hypes_seasonal_bench_period (benchmark_code, period_days) dropped: the
-- code-first PK prefix already serves benchmark_code-filtered lookups.
DROP INDEX IF EXISTS analysis.idx_hypes_seasonal_bench_period;
CREATE INDEX IF NOT EXISTS idx_hypes_seasonal_industry
    ON analysis.industry_hypes_seasonal (industry_id, benchmark_code, period_days);

COMMENT ON TABLE  analysis.industry_hypes_seasonal IS 'Seasonal (monthly) top-5 HYPE + bottom-5 DRAIN industry rankings. One row per (benchmark_code, season_qkey, period_days, rank_side, rank). peak_metric_value = MAX (HYPE) or MIN (DRAIN) of the per-date metric_value within the month. Built by analyze.industry_sentiments.hypes_and_drains after the per-date table. The Market Trend chart uses this to determine which industry curves to show (daily plot, seasonal ranking selection with fading/disappearing logic).';

COMMENT ON COLUMN analysis.industry_hypes_seasonal.season_qkey IS 'Calendar month key, format YYYY-MM (e.g. 2026-08 for August 2026).';
COMMENT ON COLUMN analysis.industry_hypes_seasonal.season_month IS 'Calendar month number 1..12.';
COMMENT ON COLUMN analysis.industry_hypes_seasonal.peak_metric_value IS 'Peak hype within the month. HYPE = MAX of daily metric_value, DRAIN = MIN. metric_value is hype (equal weighting) or hype × shared_trading_amt (amt weighting). Used for ranking which industries are the top-5 / bottom-5 for the month.';
