-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Implied-volatility skew statistics per expiry group — how implied vol tilts across strikes.
-- tags: options, 期权, implied volatility, 隐含波动率, skew, 偏度, iv smile, 波动率微笑


CREATE TABLE IF NOT EXISTS analysis.options_iv_skew_stats (
    date                      DATE          NOT NULL,
    option_type               SMALLINT      NOT NULL,  -- label code (static.label_dim 'option_type'): 0=CALL 1=PUT
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    atm_iv                      NUMERIC(10,2),   -- IV (vol pts %) of contract closest to moneyness 1.0
    iv_call25                   NUMERIC(10,2),   -- IV (vol pts %) of OTM CALL nearest |delta| = 0.25
    iv_put25                    NUMERIC(10,2),   -- IV (vol pts %) of OTM PUT nearest |delta| = 0.25
    risk_reversal_25d           NUMERIC(10,2),   -- iv_call25 - iv_put25 (negative = puts richer)
    put_skew_25d                NUMERIC(10,2),   -- iv_put25 - atm_iv
    call_skew_25d               NUMERIC(10,2),   -- iv_call25 - atm_iv
    iv_call10                   NUMERIC(10,2),   -- IV (vol pts %) of OTM CALL nearest |delta| = 0.10
    iv_put10                    NUMERIC(10,2),   -- IV (vol pts %) of OTM PUT nearest |delta| = 0.10
    risk_reversal_10d           NUMERIC(10,2),   -- iv_call10 - iv_put10 (deeper wing; NULL when no near-0.10 contract)
    smile_skewness              NUMERIC(10,2),   -- OI-weighted 3rd moment of IV across strikes (per option_type)

    rr25_ma5                     NUMERIC(10,2),
    rr25_ma20                    NUMERIC(10,2),
    rr25_ma60                    NUMERIC(10,2),

    rr25_std5                    NUMERIC(10,2),
    rr25_std20                   NUMERIC(10,2),
    rr25_std60                   NUMERIC(10,2),

    rr25_slope                   NUMERIC(10,2),
    rr25_ma5_slope               NUMERIC(10,2),
    rr25_ma20_slope              NUMERIC(10,2),
    rr25_ma60_slope              NUMERIC(10,2),

    corr_rr25_ma5_vs_spot_ma5    NUMERIC(10,2),
    corr_rr25_ma20_vs_spot_ma20  NUMERIC(10,2),
    corr_rr25_ma60_vs_spot_ma60  NUMERIC(10,2),

    CONSTRAINT pk_options_iv_skew_stats
        PRIMARY KEY (underlying_code, date, option_type, expiry_date),
    CONSTRAINT fk_options_iv_skew_stats_expiry
        FOREIGN KEY (underlying_code, date, option_type, expiry_date)
        REFERENCES analysis.options_expiry_identity
            (underlying_code, date, option_type, expiry_date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by underlying_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_iv_skew_stats', 8);

-- idx_options_iv_skew_stats_underlying_date (underlying_code, date) dropped:
-- a prefix of the underlying_code-first PK, which already serves per-underlying lookups.

CREATE INDEX IF NOT EXISTS idx_options_iv_skew_stats_expiry
    ON analysis.options_iv_skew_stats (underlying_code, expiry_date, date);

COMMENT ON TABLE  analysis.options_iv_skew_stats              IS 'Per-(underlying_code, date, option_type, expiry_date) store of implied-volatility skew statistics for option expiry groups, derived from implied_vol in stats.options_greeks (calibrated from option premiums via Black-76). All IV/skew values are in vol points (percent). Unlike options_skewness_stats (OI-weighted mean moneyness — a positioning metric), this is a pricing metric. FK -> analysis.options_expiry_identity. Built by analysis.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.atm_iv       IS 'IV (vol points, %) of the contract with moneyness (strike/spot) closest to 1.0 in the expiry group.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_call25    IS 'IV (vol points, %) of the OTM CALL contract with delta nearest 0.25.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_put25     IS 'IV (vol points, %) of the OTM PUT contract with delta nearest -0.25.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.risk_reversal_25d IS '25-delta risk reversal: iv_call25 - iv_put25 (vol points). Negative = OTM puts richer than OTM calls = downside hedging demand.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.put_skew_25d  IS 'iv_put25 - atm_iv (vol points); premium paid for downside protection.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.call_skew_25d IS 'iv_call25 - atm_iv (vol points); upside speculation premium.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_call10    IS 'IV (vol points, %) of the OTM CALL contract with delta nearest 0.10 (deeper wing; sparser strike grids may have no such contract -> NULL).';
COMMENT ON COLUMN analysis.options_iv_skew_stats.iv_put10     IS 'IV (vol points, %) of the OTM PUT contract with delta nearest -0.10 (deeper wing; sparser strike grids may have no such contract -> NULL).';
COMMENT ON COLUMN analysis.options_iv_skew_stats.risk_reversal_10d IS '10-delta risk reversal: iv_call10 - iv_put10 (vol points). More crash-sensitive / steeper in panics than risk_reversal_25d, but thinner quotes and staler marks on deep wings.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.smile_skewness IS 'OI-weighted 3rd standardized moment of IV across the expiry group''s strikes, per option_type. Negative = higher IV on downside.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma5     IS '5-day rolling MA of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma20    IS '20-day rolling MA of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma60    IS '60-day rolling MA of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_std5    IS '5-day rolling STD of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_std20   IS '20-day rolling STD of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_std60   IS '60-day rolling STD of risk_reversal_25d.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_slope   IS 'Linear regression slope of risk_reversal_25d vs time over full history of the expiry group.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma5_slope   IS 'Linear regression slope of rr25_ma5 vs time.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma20_slope  IS 'Linear regression slope of rr25_ma20 vs time.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.rr25_ma60_slope  IS 'Linear regression slope of rr25_ma60 vs time.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.corr_rr25_ma5_vs_spot_ma5   IS 'Whole-period cumulative correlation between MA5 of risk_reversal_25d and MA5 of spot price.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.corr_rr25_ma20_vs_spot_ma20 IS 'Whole-period cumulative correlation between MA20 of risk_reversal_25d and MA20 of spot price.';
COMMENT ON COLUMN analysis.options_iv_skew_stats.corr_rr25_ma60_vs_spot_ma60 IS 'Whole-period cumulative correlation between MA60 of risk_reversal_25d and MA60 of spot price.';
