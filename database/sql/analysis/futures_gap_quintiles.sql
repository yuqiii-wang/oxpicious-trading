
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
--  Rebuilt (upsert on the natural PK) by analysis.futures on every run.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: How the futures basis moves over the following days per starting-gap quintile — the basis-convergence pattern summary per contract type.
-- tags: futures, 期货, basis, 基差, quintile, 分位, convergence, 收敛

CREATE TABLE IF NOT EXISTS analysis.futures_gap_quintiles (
    asof_date                        DATE          NOT NULL,
    contract_type                    SMALLINT      NOT NULL,  -- label code (static.label_dim 'contract_type'): 0=index 1=bond
    horizon_days                     INTEGER       NOT NULL,
    quintile                         INTEGER       NOT NULL,
    n_obs                            INTEGER,
    mean_gap_bps                     NUMERIC(18,4),
    mean_fwd_chg_bps                 NUMERIC(18,4),
    CONSTRAINT pk_futures_gap_quintiles PRIMARY KEY (asof_date, contract_type, horizon_days, quintile),
    CONSTRAINT ck_futures_gap_quintiles_quintile CHECK (quintile BETWEEN 1 AND 5)
);

COMMENT ON TABLE  analysis.futures_gap_quintiles                    IS 'Basis-convergence quintile summary for analysis.futures. Mean gap (bps) and mean forward gap change over the next horizon_days trading days (bps) per gap quintile (1 = lowest gap, 5 = highest), pooled per contract_type. High-quintile NEGATIVE forward change / low-quintile POSITIVE change = contrarian convergence of the futures basis toward the underlying. asof_date-stamped snapshots, one set per pipeline run. Built by analysis.futures; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.futures_gap_quintiles.asof_date          IS 'Snapshot date = the latest date in analysis.futures_ext when the run computed the summary.';
COMMENT ON COLUMN analysis.futures_gap_quintiles.contract_type      IS 'Contract class pooled for the quintiles, as its static.label_dim (''contract_type'' domain) SMALLINT code: 0 = ''index'' (IC/IF/IH/IM), 1 = ''bond'' (T/TF/TL/TS).';
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
     'Basis-convergence quintile summary for analysis.futures. One row per (asof_date, contract_type, horizon_days, quintile): the pooled gap distribution is split into 5 quintiles and each records its mean gap (bps) and mean gap change over the next horizon_days trading days (bps). Quantifies the contrarian convergence of the futures basis (premium quintiles narrow, discount quintiles widen toward zero). asof_date-stamped snapshots accumulate a history of the convergence stats. Rebuilt by analysis.futures on every run; all INSERTs in Python per project rule.')
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
     'Futures basis and correlation analysis. One row per (date, code) comparing each CFFEX futures contract against its underlying: index futures (IC/IF/IH/IM) vs underlying index close; bond futures (T/TF/TL/TS) vs treasury yield curve converted to a zero-coupon bond price proxy (100 / (1 + y/2)^(2*tenor_years)). Stores the basis gap (price + MA5), its 1st-order derivative gap_changing_rate (negative = basis converging toward underlying, positive = diverging), 20-day rolling correlations (price + MA5), rolling maximums of the basis over 20-day and 60-day windows (gap_max_price_vs_underlying_over_Ndays) for identifying historical basis extremes, and a 60-day rolling AR(1) of the basis (gap_ar1_slope_over_60days; slope < 1 = basis mean-reverts toward the underlying, with the implied gap_half_life_over_60days in trading days). Built by analysis.futures (--force = DELETE + chunked COPY; default = incremental missing-(date,code) upsert); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
