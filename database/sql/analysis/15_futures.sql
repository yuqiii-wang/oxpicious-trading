-- ============================================================================
--  Futures Basis Analysis — per-(date, code) futures-vs-underlying metrics.
--
--  Compares each CFFEX futures contract's price against its underlying:
--    Index futures (IC/IF/IH/IM) -> underlying index close
--      (IC->000905, IF->000300, IH->000016, IM->000852)
--    Bond futures  (T/TF/TL/TS)  -> treasury yield curve converted to a
--      zero-coupon bond price proxy: 100 / (1 + y/2)^(2*tenor_years)
--      (T->cb_10y, TF->cb_5y, TL->cb_30y, TS->cb_2y from stats.debt_treasury)
--
--  Table: analysis.futures_ext
--    PK: (date, code), FK -> stats.futures_identity(date, code)
--
--  COLUMNS
--    gap_price_vs_underlying — Basis: (futures_close - underlying_price) /
--      underlying_price. Positive = futures above underlying (contango);
--      negative = discount (backwardation).
--
--    gap_price_ma5_vs_underlying_ma5 — Same on the 5-day MA (smoothed
--      basis, removes daily noise).
--
--    gap_changing_rate_price_vs_underlying — 1st-order derivative of the
--      basis (day-over-day diff per contract). Positive = basis widening
--      (futures DIVERGING from underlying); negative = basis narrowing
--      (CONVERGING toward underlying).
--
--    gap_changing_rate_price_ma5_vs_underlying_ma5 — Same derivative on
--      the MA5 basis.
--
--    corr_price_vs_underlying — 20-day rolling correlation between
--      futures_close and underlying_price. NULL for the first 19 days of
--      each contract.
--
--    corr_price_ma5_vs_underlying_ma5 — 20-day rolling correlation between
--      futures_ma5 and underlying_ma5.
--
--    gap_max_price_vs_underlying_over_20days — Rolling maximum of
--      gap_price_vs_underlying over the trailing 20 trading days per
--      contract. Useful for identifying historical basis extremes over
--      the past month.
--
--    gap_max_price_vs_underlying_over_60days — Same rolling maximum over
--      the trailing 60 trading days (quarterly window).
--
--    gap_ar1_slope_over_60days — Rolling AR(1) slope of the basis:
--      gap_t = a + b * gap_{t-1} fitted over the trailing 60 trading
--      days per contract. b < 1 = the basis mean-reverts toward the
--      underlying (contrarian convergence); b ~ 1 = random walk;
--      b > 1 = diverging. NULL for the first 60 days of each contract.
--
--    gap_half_life_over_60days — Implied mean-reversion half-life
--      ln(0.5) / ln(b) in trading days from the AR(1) slope. NULL
--      unless 0 < b < 1. Index futures historically run ~14-30 days;
--      bond futures (T/TF/TL/TS) are near random walk against the
--      zero-coupon price proxy.
--
--  Table: analysis.futures_gap_quintiles
--    Small per-run snapshot quantifying the contrarian convergence:
--    the pooled basis-gap distribution per contract_type is split into
--    5 quintiles and each quintile records its mean gap (bps) and mean
--    gap change over the next 5/20 trading days (bps). PK:
--    (asof_date, contract_type, horizon_days, quintile); asof_date
--    stamps each run so consecutive runs accumulate a snapshot history.
--
--  SOURCE
--    stats.futures_identity (contract identity + underlying_code),
--    stats.futures_basic_stats (futures close),
--    stats.index_basic_stats (index closes),
--    stats.debt_treasury (bond yield curve).
--
--  POPULATION
--    analyze.futures (Python module; --force = DELETE + chunked COPY,
--    default = incremental missing-(date,code) upsert). Per project rule,
--    ALL INSERTs are in Python — no raw INSERT...SELECT SQL in this file.
--
--  Register in analysis.analysis_identity (name='futures_ext').
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.futures_ext (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    underlying_code           TEXT          NOT NULL,

    gap_price_vs_underlying        NUMERIC(18,4),
    gap_price_ma5_vs_underlying_ma5        NUMERIC(18,4),
    gap_changing_rate_price_vs_underlying        NUMERIC(18,4),
    gap_changing_rate_price_ma5_vs_underlying_ma5        NUMERIC(18,4),

    corr_price_vs_underlying        NUMERIC(18,4),
    corr_price_ma5_vs_underlying_ma5        NUMERIC(18,4),

    gap_max_price_vs_underlying_over_20days        NUMERIC(18,4),
    gap_max_price_vs_underlying_over_60days        NUMERIC(18,4),

    gap_ar1_slope_over_60days                      NUMERIC(18,4),
    gap_half_life_over_60days                      NUMERIC(18,4),

    CONSTRAINT pk_futures_ext PRIMARY KEY (code, date),
    CONSTRAINT fk_futures_ext_date_code FOREIGN KEY (code, date) REFERENCES stats.futures_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'futures_ext', 8);

-- Indexes for common access patterns:
--   1. Per-contract time series.
--   2. Per-underlying cross-sectional scan (all contracts of a product).
-- idx_futures_ext_code_date (code, date) dropped:
-- identical to the code-first PK, which already serves per-contract lookups.

CREATE INDEX IF NOT EXISTS idx_futures_ext_underlying_date
    ON analysis.futures_ext (underlying_code, date);

COMMENT ON TABLE  analysis.futures_ext                          IS 'Futures basis and correlation analysis. One row per (code, date) comparing futures price against underlying (index close for index futures, treasury yield-derived bond price for bond futures). gap_price_vs_underlying = (futures_close - underlying_price) / underlying_price (basis). gap_changing_rate = day-over-day change in the basis (1st-order derivative: negative = converging, positive = diverging). corr = 20-day rolling correlation. gap_max_price_vs_underlying_over_Ndays = rolling maximum of the basis over N trailing trading days per contract. gap_ar1_slope_over_60days / gap_half_life_over_60days = rolling AR(1) of the basis over 60 days (slope < 1 = mean-reverting toward the underlying; half-life in trading days). Built by analyze.futures; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.futures_ext.date                     IS 'Trading date.';
COMMENT ON COLUMN analysis.futures_ext.code                     IS 'Futures contract code, e.g. "IC2607", "T2609". FK -> stats.futures_identity.';
COMMENT ON COLUMN analysis.futures_ext.underlying_code          IS 'Underlying asset code: index futures map to stock index codes (e.g. IF->000300); bond futures use synthetic codes (e.g. T->T10).';
COMMENT ON COLUMN analysis.futures_ext.gap_price_vs_underlying  IS 'Basis: (futures_close - underlying_price) / underlying_price. Positive = futures above underlying (contango); negative = discount (backwardation).';
COMMENT ON COLUMN analysis.futures_ext.gap_price_ma5_vs_underlying_ma5 IS 'MA5 basis: (futures_ma5 - underlying_ma5) / underlying_ma5 (smoothed basis).';
COMMENT ON COLUMN analysis.futures_ext.gap_changing_rate_price_vs_underlying IS '1st-order derivative of the basis (day-over-day diff per contract). Positive = basis widening (diverging from underlying); negative = basis narrowing (converging toward underlying). NULL on each contract''s first day.';
COMMENT ON COLUMN analysis.futures_ext.gap_changing_rate_price_ma5_vs_underlying_ma5 IS 'Same 1st-order derivative on the MA5 basis.';
COMMENT ON COLUMN analysis.futures_ext.corr_price_vs_underlying IS '20-day rolling correlation between futures_close and underlying_price. NULL for the first 19 days of each contract (insufficient window).';
COMMENT ON COLUMN analysis.futures_ext.corr_price_ma5_vs_underlying_ma5 IS '20-day rolling correlation between futures_ma5 and underlying_ma5.';
COMMENT ON COLUMN analysis.futures_ext.gap_max_price_vs_underlying_over_20days IS 'Rolling maximum of gap_price_vs_underlying over the trailing 20 trading days per contract. Useful for identifying recent basis extremes (e.g. the widest contango or steepest backwardation in the past month). NULL for the first 19 days of each contract.';
COMMENT ON COLUMN analysis.futures_ext.gap_max_price_vs_underlying_over_60days IS 'Rolling maximum of gap_price_vs_underlying over the trailing 60 trading days per contract (quarterly window). Captures medium-term basis extremes. NULL for the first 59 days of each contract.';
COMMENT ON COLUMN analysis.futures_ext.gap_ar1_slope_over_60days IS 'Rolling AR(1) slope of the basis: gap_t = a + b * gap_{t-1} over the trailing 60 trading days per contract. b < 1 = basis mean-reverts toward the underlying (contrarian convergence); b ~ 1 = random walk; b > 1 = diverging. NULL for the first 60 days of each contract.';
COMMENT ON COLUMN analysis.futures_ext.gap_half_life_over_60days IS 'Implied mean-reversion half-life ln(0.5)/ln(b) in trading days from the 60-day AR(1) slope b. NULL unless 0 < b < 1. Index futures (IC/IF/IH/IM) historically ~14-30 days; bond futures near random walk against the zero-coupon price proxy.';

-- ----------------------------------------------------------------------------
--  analysis.futures_gap_quintiles — basis-convergence quintile summary
--
--  Small per-run snapshot: the pooled gap_price_vs_underlying
--  distribution across all contracts of a contract_type (index / bond)
--  is split into 5 quintiles; each quintile records its mean gap (bps)
--  and the mean gap change over the next horizon_days trading days
--  (bps). Contrarian convergence shows up as NEGATIVE forward change
--  in the high (premium) quintiles and POSITIVE change in the low
--  (discount) quintiles. Observations without a complete forward
--  window (the last horizon_days days of the sample) are excluded.
--  PK: (asof_date, contract_type, horizon_days, quintile); asof_date
--  stamps each pipeline run, so consecutive runs accumulate snapshots.
--  Rebuilt (upsert on the natural PK) by analyze.futures on every run.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.futures_gap_quintiles (
    asof_date                        DATE          NOT NULL,
    contract_type                    TEXT          NOT NULL,
    horizon_days                     INTEGER       NOT NULL,
    quintile                         INTEGER       NOT NULL,
    n_obs                            INTEGER,
    mean_gap_bps                     NUMERIC(18,4),
    mean_fwd_chg_bps                 NUMERIC(18,4),
    CONSTRAINT pk_futures_gap_quintiles PRIMARY KEY (asof_date, contract_type, horizon_days, quintile),
    CONSTRAINT ck_futures_gap_quintiles_quintile CHECK (quintile BETWEEN 1 AND 5)
);

COMMENT ON TABLE  analysis.futures_gap_quintiles                    IS 'Basis-convergence quintile summary for analyze.futures. Mean gap (bps) and mean forward gap change over the next horizon_days trading days (bps) per gap quintile (1 = lowest gap, 5 = highest), pooled per contract_type. High-quintile NEGATIVE forward change / low-quintile POSITIVE change = contrarian convergence of the futures basis toward the underlying. asof_date-stamped snapshots, one set per pipeline run. Built by analyze.futures; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.futures_gap_quintiles.asof_date          IS 'Snapshot date = the latest date in analysis.futures_ext when the run computed the summary.';
COMMENT ON COLUMN analysis.futures_gap_quintiles.contract_type      IS 'Contract type pooled for the quintiles: ''index'' (IC/IF/IH/IM) or ''bond'' (T/TF/TL/TS).';
COMMENT ON COLUMN analysis.futures_gap_quintiles.horizon_days       IS 'Forward horizon in trading days for the gap change (5 or 20).';
COMMENT ON COLUMN analysis.futures_gap_quintiles.quintile           IS 'Gap quintile bucket 1..5 (1 = most negative basis, 5 = most positive basis).';
COMMENT ON COLUMN analysis.futures_gap_quintiles.n_obs              IS 'Number of (date, contract) observations in the quintile with a complete forward window.';
COMMENT ON COLUMN analysis.futures_gap_quintiles.mean_gap_bps       IS 'Mean gap_price_vs_underlying of the quintile, in basis points.';
COMMENT ON COLUMN analysis.futures_gap_quintiles.mean_fwd_chg_bps   IS 'Mean change of the gap over the next horizon_days trading days, in basis points. Negative in high quintiles (premium narrowing) and positive in low quintiles (discount widening toward zero) = contrarian convergence.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('futures_gap_quintiles', 'analysis.futures_gap_quintiles', NULL, NOW(),
     'Basis-convergence quintile summary for analyze.futures. One row per (asof_date, contract_type, horizon_days, quintile): the pooled gap distribution is split into 5 quintiles and each records its mean gap (bps) and mean gap change over the next horizon_days trading days (bps). Quantifies the contrarian convergence of the futures basis (premium quintiles narrow, discount quintiles widen toward zero). asof_date-stamped snapshots accumulate a history of the convergence stats. Rebuilt by analyze.futures on every run; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('futures_ext', 'futures_ext', NULL, NOW(),
     'Futures basis and correlation analysis. One row per (date, code) comparing each CFFEX futures contract against its underlying: index futures (IC/IF/IH/IM) vs underlying index close; bond futures (T/TF/TL/TS) vs treasury yield curve converted to a zero-coupon bond price proxy (100 / (1 + y/2)^(2*tenor_years)). Stores the basis gap (price + MA5), its 1st-order derivative gap_changing_rate (negative = basis converging toward underlying, positive = diverging), 20-day rolling correlations (price + MA5), rolling maximums of the basis over 20-day and 60-day windows (gap_max_price_vs_underlying_over_Ndays) for identifying historical basis extremes, and a 60-day rolling AR(1) of the basis (gap_ar1_slope_over_60days; slope < 1 = basis mean-reverts toward the underlying, with the implied gap_half_life_over_60days in trading days). Built by analyze.futures (--force = DELETE + chunked COPY; default = incremental missing-(date,code) upsert); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
