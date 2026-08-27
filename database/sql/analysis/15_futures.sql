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
DROP INDEX IF EXISTS analysis.idx_futures_ext_code_date;

CREATE INDEX IF NOT EXISTS idx_futures_ext_underlying_date
    ON analysis.futures_ext (underlying_code, date);

-- ----------------------------------------------------------------------------
--  Migration: stats.futures_ext -> analysis.futures_ext on existing installs.
--  Idempotent (moves the table only when it still lives in stats; preserves
--  data, PK, FK, and indexes). Fresh installs create the table directly in
--  analysis via the CREATE TABLE above, so this DO block is a no-op.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'stats' AND table_name = 'futures_ext'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'analysis' AND table_name = 'futures_ext'
    ) THEN
        ALTER TABLE stats.futures_ext SET SCHEMA analysis;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
--  Migration: add gap_max_* columns to existing installs.
--  Idempotent (fresh installs get the columns from CREATE TABLE above).
--  Back-filled by re-running the Python populator with --force.
-- ----------------------------------------------------------------------------
ALTER TABLE analysis.futures_ext
    ADD COLUMN IF NOT EXISTS gap_max_price_vs_underlying_over_20days NUMERIC(18,4),
    ADD COLUMN IF NOT EXISTS gap_max_price_vs_underlying_over_60days NUMERIC(18,4);

COMMENT ON TABLE  analysis.futures_ext                          IS 'Futures basis and correlation analysis. One row per (code, date) comparing futures price against underlying (index close for index futures, treasury yield-derived bond price for bond futures). gap_price_vs_underlying = (futures_close - underlying_price) / underlying_price (basis). gap_changing_rate = day-over-day change in the basis (1st-order derivative: negative = converging, positive = diverging). corr = 20-day rolling correlation. gap_max_price_vs_underlying_over_Ndays = rolling maximum of the basis over N trailing trading days per contract. Built by analyze.futures; all INSERTs in Python per project rule.';
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

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('futures_ext', 'futures_ext', NULL, NOW(),
     'Futures basis and correlation analysis. One row per (date, code) comparing each CFFEX futures contract against its underlying: index futures (IC/IF/IH/IM) vs underlying index close; bond futures (T/TF/TL/TS) vs treasury yield curve converted to a zero-coupon bond price proxy (100 / (1 + y/2)^(2*tenor_years)). Stores the basis gap (price + MA5), its 1st-order derivative gap_changing_rate (negative = basis converging toward underlying, positive = diverging), 20-day rolling correlations (price + MA5), and rolling maximums of the basis over 20-day and 60-day windows (gap_max_price_vs_underlying_over_Ndays) for identifying historical basis extremes. Built by analyze.futures (--force = DELETE + chunked COPY; default = incremental missing-(date,code) upsert); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
