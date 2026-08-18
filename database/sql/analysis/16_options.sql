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

-- ============================================================================
--  Options Stats Before Expiry — per-(date, option_type, underlying_code,
--  expiry_date) rolling skew statistics that otherwise require online
--  computation.
--
--  Store for precomputed rolling stats of the Volatility Smile panel
--  (VolSmilePanel.tsx). Stats are computed per expiry set (all active
--  contracts sharing the same option_type + underlying_code + expiry_date):
--
--    S   = underlying_close / 1000          (spot; 厘 -> yuan/index points)
--    E[M]= Σ(max(1, OI) · strike/S) / Σ(max(1, OI))   (OI-weighted moneyness)
--    S*  = S · E[M]                          (skew-adjusted price)
--
--  One row per expiry group (option_type distinguishes CALL/PUT).
--  The frontend/API joins per (date, option_type, underlying_code,
--  expiry_date) instead of recomputing online.
--
--  Valid contract rows: implied_vol IN (0, 5), strike_price > 0,
--  underlying_close > 0, expiry_date >= date (active only), and the expiry
--  group needs >= 3 valid rows (matching the panel's minimum).
--
--  Table: analysis.options_stats_before_expiry
--    PK: (date, option_type, underlying_code, expiry_date)
--    FK: (date, option_type, underlying_code, expiry_date)
--        -> analysis.options_expiry_identity
--
--  COLUMNS (gap direction: "gap from X" = today's S* − X)
--    today_gap_from_today_spot — S* − S. Negative = smile mass tilted to
--      OTM puts/low strikes (negative skew); positive = call-side tilt.
--
--    today_gap_from_max_before_expiry — S* − max(S* over [date, expiry]).
--      <= 0 by construction. How far today's skew sits BELOW the remaining
--      lifetime high. NULL while the expiry has not matured (window not
--      yet complete).
--
--    today_gap_from_min_before_expiry — S* − min(S* over [date, expiry]).
--      >= 0 by construction. How far today's skew sits ABOVE the remaining
--      lifetime low. NULL while the expiry has not matured.
--
--  SOURCE
--    stats.options_terms (underlying_code, expiry_date, option_type),
--    stats.options_strike (strike_price, 厘),
--    stats.options_settlement (underlying_close, 厘),
--    stats.options_volume_oi (open_interest),
--    stats.options_greeks (implied_vol).
--
--    NOTE: SZSE ETF options are stored on the same 厘 scale as their mapped
--    index code (ETF yuan × 1000 ≈ index points), so moneyness
--    strike/underlying_close is scale-free per row and SZSE + CFFEX rows of
--    the same underlying are comparable. Stats are computed per exact
--    expiry_date because CFFEX (3rd Friday) and SZSE (4th Wednesday)
--    expiries differ within the same calendar month.
--
--  POPULATION
--    analyze.options (Python module; --force = DELETE + chunked COPY,
--    default = incremental: missing expiry groups + backfill
--    of NULL future-window gaps once an expiry matures).
--    Per project rule, ALL INSERTs are in Python — no raw INSERT...SELECT
--    SQL in this file.
--
--  Register in analysis.analysis_identity (name='options_stats_before_expiry').
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.options_stats_before_expiry (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    today_gap_from_today_spot        NUMERIC(10,2),
    today_gap_from_max_before_expiry NUMERIC(10,2),
    today_gap_from_min_before_expiry NUMERIC(10,2),
    max_date_before_expiry DATE,
    min_date_before_expiry DATE,

    CONSTRAINT pk_options_stats_before_expiry
        PRIMARY KEY (date, option_type, underlying_code, expiry_date),
    CONSTRAINT fk_options_stats_before_expiry_expiry
        FOREIGN KEY (date, option_type, underlying_code, expiry_date)
        REFERENCES analysis.options_expiry_identity
            (date, option_type, underlying_code, expiry_date)
);

-- Indexes for common access patterns:
--   1. Per-underlying time series (panel loads one underlying's history).
--   2. Per-expiry-set scan (all dates of one expiry group).
CREATE INDEX IF NOT EXISTS idx_options_stats_before_expiry_underlying_date
    ON analysis.options_stats_before_expiry (underlying_code, date);

CREATE INDEX IF NOT EXISTS idx_options_stats_before_expiry_expiry
    ON analysis.options_stats_before_expiry (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_stats_before_expiry                        IS 'Per-(date, option_type, underlying_code, expiry_date) store of precomputed options rolling skew statistics for expiry groups. One row per (date, option_type, underlying_code, expiry_date) — S* = (underlying_close/1000) · E[M] where E[M] is the OI-weighted mean moneyness over the expiry group''s active contracts (valid IV in (0,5), >=3 rows). Gaps: today_gap_from_today_spot = S* − spot; today_gap_from_max/min_before_expiry = S* − max/min(S* over [date, expiry]) — NULL until the expiry has matured. Dates: max_date/min_date_before_expiry = dates of the future max/min skew_price — NULL until matured. FK -> analysis.options_expiry_identity. Built by analyze.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_stats_before_expiry.date                    IS 'Trading date.';
COMMENT ON COLUMN analysis.options_stats_before_expiry.option_type            IS 'Option type: CALL or PUT.';
COMMENT ON COLUMN analysis.options_stats_before_expiry.underlying_code        IS 'Underlying code (unified index codes; SZSE ETF options mapped via ETF->Index, e.g. 159919->000300).';
COMMENT ON COLUMN analysis.options_stats_before_expiry.expiry_date            IS 'Exact contract expiry date. CFFEX (3rd Friday) and SZSE (4th Wednesday) expiries differ within a month, so expiry_date — not the month label — is the true expiry-set key.';
COMMENT ON COLUMN analysis.options_stats_before_expiry.today_gap_from_today_spot        IS 'S* − S: skew-adjusted price minus spot. Negative = smile mass tilted to low strikes (put-side/negative skew); positive = call-side tilt.';
COMMENT ON COLUMN analysis.options_stats_before_expiry.today_gap_from_max_before_expiry IS 'S* − max(S* over [date, expiry]) (<= 0). Distance below the remaining-lifetime high of this expiry group''s skew price. NULL while the expiry is not yet matured.';
COMMENT ON COLUMN analysis.options_stats_before_expiry.today_gap_from_min_before_expiry IS 'S* − min(S* over [date, expiry]) (>= 0). Distance above the remaining-lifetime low of this expiry group''s skew price. NULL while the expiry is not yet matured.';
COMMENT ON COLUMN analysis.options_stats_before_expiry.max_date_before_expiry IS 'Date within the future window [date, expiry] when skew_price (S*) reached its maximum. NULL while the expiry is not yet matured.';
COMMENT ON COLUMN analysis.options_stats_before_expiry.min_date_before_expiry IS 'Date within the future window [date, expiry] when skew_price (S*) reached its minimum. NULL while the expiry is not yet matured.';

CREATE TABLE IF NOT EXISTS analysis.options_skewness_stats (
    date                      DATE          NOT NULL,
    option_type               TEXT          NOT NULL
        CHECK (option_type IN ('CALL','PUT')),
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

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
    ('options_stats_before_expiry', 'options_stats_before_expiry', NULL, NOW(),
     'Per-(date, option_type, underlying_code, expiry_date) store of precomputed options rolling skew statistics (FK -> analysis.options_expiry_identity), so the frontend/API can join per expiry group instead of recomputing online. Stats are computed per expiry set (date, option_type, underlying_code, expiry_date): S* = (underlying_close/1000) · E[M], E[M] = Σ(max(1,OI)·strike/S) / Σ(max(1,OI)) over the set''s valid-IV contracts (>=3 rows). Stores three gaps (direction: today − reference): today_gap_from_today_spot = S* − spot; today_gap_from_max/min_before_expiry = S* − max/min(S* over the future window [date, expiry]) — computed only for matured expiries (NULL otherwise). Also stores max_date/min_date_before_expiry — the dates within the future window when skew_price reached its max/min (NULL for non-matured expiries). Built by analyze.options (--force = DELETE + chunked COPY; default = incremental missing-expiry-group upsert + NULL-gap backfill on maturing); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

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

