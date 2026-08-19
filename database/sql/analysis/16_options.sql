-- ============================================================================
--  Options Expiry Identity — lookup table for (date, option_type,
--  underlying_code, expiry_date) groups. All other options analysis
--  tables have FK references to this table.
--
--  Table: analysis.options_expiry_identity
--    PK: (date, option_type, underlying_code, expiry_date)
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.options_expiry_identity (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    CONSTRAINT pk_options_expiry_identity
        PRIMARY KEY (date, option_type, underlying_code, expiry_date)
);

CREATE TABLE IF NOT EXISTS analysis.options_skewness_stats (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    count_skewness_curve_crossed_spot INTEGER           NOT NULL DEFAULT 0, -- count of times skewness val is below/above spot, today crossed to above/below, accumulated ++ from last val from prev days, if not happened, keep prev day val

    skewness_ma5                NUMERIC(10,2),
    skewness_ma20               NUMERIC(10,2),
    skewness_ma60               NUMERIC(10,2),

    skewness_std5               NUMERIC(10,2),
    skewness_std20              NUMERIC(10,2),
    skewness_std60              NUMERIC(10,2),

    gap_skewness_vs_spot_ma5    NUMERIC(10,2),
    gap_skewness_vs_spot_ma20   NUMERIC(10,2),
    gap_skewness_vs_spot_ma60   NUMERIC(10,2),

    gap_skewness_vs_spot_slope  NUMERIC(10,2),
    gap_skewness_vs_spot_ma5_slope   NUMERIC(10,2),
    gap_skewness_vs_spot_ma20_slope  NUMERIC(10,2),
    gap_skewness_vs_spot_ma60_slope  NUMERIC(10,2),

    corr_skewness_ma5_vs_spot_ma5       NUMERIC(10,2),
    corr_skewness_ma20_vs_spot_ma20       NUMERIC(10,2),
    corr_skewness_ma60_vs_spot_ma60       NUMERIC(10,2),

    CONSTRAINT pk_options_skewness_stats
        PRIMARY KEY (date, option_type, underlying_code, expiry_date),
    CONSTRAINT fk_options_skewness_stats_expiry
        FOREIGN KEY (date, option_type, underlying_code, expiry_date)
        REFERENCES analysis.options_expiry_identity
            (date, option_type, underlying_code, expiry_date)
);

-- Indexes for common access patterns:
--   1. Per-underlying time series (panel loads one underlying's history).
--   2. Per-expiry scan (all dates of one expiry group).
CREATE INDEX IF NOT EXISTS idx_options_skewness_stats_underlying_date
    ON analysis.options_skewness_stats (underlying_code, date);

CREATE INDEX IF NOT EXISTS idx_options_skewness_stats_expiry
    ON analysis.options_skewness_stats (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_skewness_stats              IS 'Per-(date, option_type, underlying_code, expiry_date) store of precomputed rolling skewness statistics for option expiry groups. "Skewness" = OI-weighted mean moneyness (strike_price / underlying_close) across all valid contracts of an expiry group — how far the strike structure sits from spot. Rolling windows (5/20/60 days) compute MA, STD, gap-from-spot (skewness_MA - 1), slope of gap, and correlation with spot. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_skewness_stats.date                    IS 'Trading date.';
COMMENT ON COLUMN analysis.options_skewness_stats.option_type            IS 'Option type: CALL or PUT.';
COMMENT ON COLUMN analysis.options_skewness_stats.underlying_code        IS 'Underlying code (unified index codes; SZSE ETF options mapped via ETF->Index, e.g. 159919->000300).';
COMMENT ON COLUMN analysis.options_skewness_stats.expiry_date            IS 'Exact contract expiry date. CFFEX (3rd Friday) and SZSE (4th Wednesday) expiries differ within a month.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_ma5            IS '5-day rolling MA of OI-weighted mean moneyness (strike_price / underlying_close) across the expiry group''s contracts.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_ma20           IS '20-day rolling MA of OI-weighted mean moneyness.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_ma60           IS '60-day rolling MA of OI-weighted mean moneyness.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_std5          IS '5-day rolling STD of OI-weighted mean moneyness.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_std20         IS '20-day rolling STD of OI-weighted mean moneyness.';
COMMENT ON COLUMN analysis.options_skewness_stats.skewness_std60         IS '60-day rolling STD of OI-weighted mean moneyness.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma5   IS '5-day rolling gap from spot (skewness_ma5 - 1); positive = strike above spot, negative = below.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma20  IS '20-day rolling gap from spot (skewness_ma20 - 1).';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma60  IS '60-day rolling gap from spot (skewness_ma60 - 1).';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_slope       IS 'Linear regression slope of (skewness - 1) vs time over full history; trend of moneyness gap.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma5_slope   IS 'Linear regression slope of gap_skewness_vs_spot_ma5 vs time.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma20_slope  IS 'Linear regression slope of gap_skewness_vs_spot_ma20 vs time.';
COMMENT ON COLUMN analysis.options_skewness_stats.gap_skewness_vs_spot_ma60_slope  IS 'Linear regression slope of gap_skewness_vs_spot_ma60 vs time.';
COMMENT ON COLUMN analysis.options_skewness_stats.corr_skewness_ma5_vs_spot_ma5 IS 'Whole-period correlation between MA5 of skew_price (price space) and MA5 of spot price, cumulative since first date of expiry group.';
COMMENT ON COLUMN analysis.options_skewness_stats.corr_skewness_ma20_vs_spot_ma20 IS 'Whole-period correlation between MA20 of skew_price (price space) and MA20 of spot price, cumulative since first date of expiry group.';
COMMENT ON COLUMN analysis.options_skewness_stats.corr_skewness_ma60_vs_spot_ma60 IS 'Whole-period correlation between MA60 of skew_price (price space) and MA60 of spot price, cumulative since first date of expiry group.';
COMMENT ON COLUMN analysis.options_skewness_stats.count_skewness_curve_crossed_spot IS 'Cumulative count of sign changes in (skewness − 1) for this expiry group. Increments when the gap crosses from below spot (negative) to at/above spot (non-negative), or vice versa. First day of each expiry group = 0; on each subsequent day, if the sign changed from the previous day the counter increments, otherwise it keeps the previous value.';

CREATE TABLE IF NOT EXISTS analysis.options_oi_stats (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    corr_put_call_ratio_vs_spot_ma5  NUMERIC(10,2),
    corr_put_call_ratio_vs_spot_ma20  NUMERIC(10,2),
    corr_put_call_ratio_vs_spot_ma60  NUMERIC(10,2),

    CONSTRAINT pk_options_oi_stats
        PRIMARY KEY (date, option_type, underlying_code, expiry_date),
    CONSTRAINT fk_options_oi_stats_expiry
        FOREIGN KEY (date, option_type, underlying_code, expiry_date)
        REFERENCES analysis.options_expiry_identity
            (date, option_type, underlying_code, expiry_date)
);

CREATE INDEX IF NOT EXISTS idx_options_oi_stats_underlying_date
    ON analysis.options_oi_stats (underlying_code, date);

CREATE INDEX IF NOT EXISTS idx_options_oi_stats_expiry
    ON analysis.options_oi_stats (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_oi_stats                       IS 'Per-(date, option_type, underlying_code, expiry_date) store of precomputed options OI-related statistics for expiry groups. Stores MA5/MA20/MA60 whole-period cumulative correlation between put/call OI ratio and underlying spot price. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_oi_stats.date                    IS 'Trading date.';
COMMENT ON COLUMN analysis.options_oi_stats.option_type            IS 'Option type: CALL or PUT.';
COMMENT ON COLUMN analysis.options_oi_stats.underlying_code         IS 'Underlying code (unified index codes; SZSE ETF options mapped via ETF->Index).';
COMMENT ON COLUMN analysis.options_oi_stats.expiry_date             IS 'Exact contract expiry date.';
COMMENT ON COLUMN analysis.options_oi_stats.corr_put_call_ratio_vs_spot_ma5 IS 'Whole-period cumulative Pearson correlation between 5-day MA of put/call OI ratio and 5-day MA of underlying spot price for this expiry group.';
COMMENT ON COLUMN analysis.options_oi_stats.corr_put_call_ratio_vs_spot_ma20 IS 'Whole-period cumulative correlation between MA20 of put/call OI ratio and MA20 of spot price.';
COMMENT ON COLUMN analysis.options_oi_stats.corr_put_call_ratio_vs_spot_ma60 IS 'Whole-period cumulative correlation between MA60 of put/call OI ratio and MA60 of spot price.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_skewness_stats', 'options_skewness_stats', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date) store of precomputed rolling skewness statistics for option expiry groups. "Skewness" = OI-weighted mean moneyness (strike_price / underlying_close) across all valid contracts of an expiry group. Rolling windows (5/20/60 days) compute MA, STD, gap-from-spot (skewness_MA - 1), linear regression slope of gap, and whole-period cumulative correlation with spot. For open (non-matured) expiry groups, expiry_date is set to the mean of all expiry dates per (option_type, underlying_code) to represent the aggregate open position. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_oi_stats', 'options_oi_stats', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date) store of precomputed options OI-related statistics for expiry groups. Currently stores corr_put_call_ratio_vs_spot — rolling correlation between put/call OI ratio and underlying spot price. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

