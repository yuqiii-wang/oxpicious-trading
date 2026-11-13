

-- ----------------------------------------------------------------------------
-- options_vol_index — daily 30-day model-free implied-volatility index per
-- underlying (CBOE VIX methodology adapted to settlement prices; see
-- docs/options_vol_smile_study.md). Date-granular (NOT expiry-granular) —
-- hence no FK to options_expiry_identity.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each underlying's daily 30-day implied-volatility index — the market's expected fluctuation priced by its options.
-- tags: options, 期权, vol index, 波动率指数, implied volatility, 隐含波动率, vix

CREATE TABLE IF NOT EXISTS analysis.options_vol_index (
    date                      DATE          NOT NULL,
    underlying_code           TEXT          NOT NULL,

    near_expiry_date          DATE,             -- expiry with T <= 30d used in the bracket
    far_expiry_date           DATE,             -- expiry with T > 30d used in the bracket
    dte_near                  INT,              -- calendar days to near expiry (NULL when single-expiry fallback)
    dte_far                   INT,              -- calendar days to far expiry  (NULL when single-expiry fallback)
    var_near                  NUMERIC(12,6),    -- model-free variance of the near expiry (decimal, e.g. 0.0400)
    var_far                   NUMERIC(12,6),    -- model-free variance of the far expiry
    variance_30d              NUMERIC(12,6),    -- time-interpolated 30-day variance (single-expiry value on fallback)
    vol_index_30d             NUMERIC(10,2),    -- 100 * sqrt(variance_30d), in vol points (percent)

    CONSTRAINT pk_options_vol_index
        PRIMARY KEY (underlying_code, date)
) PARTITION BY HASH (underlying_code);

SELECT public.create_hash_partitions('analysis', 'options_vol_index', 8);

COMMENT ON TABLE  analysis.options_vol_index IS 'Daily 30-day model-free implied-volatility index per underlying_code (CBOE VIX methodology adapted to exchange settlement prices): per expiry sigma^2 = (2/T) * sum_i e^(rT) * (dK_i / K_i^2) * Q(K_i) - (1/T) * (F/K0 - 1)^2 over the OTM strip (puts below the forward, calls above, average of both at K0), F = S*exp(rT), r = 0.02; two expiries bracketing 30 days are time-interpolated to a constant-30d variance, vol_index_30d = 100*sqrt(variance). No FK (date-granular). Built by analysis.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_vol_index.near_expiry_date IS 'Expiry group with the largest T <= 30 days used in the 30-day bracket.';
COMMENT ON COLUMN analysis.options_vol_index.far_expiry_date  IS 'Expiry group with the smallest T > 30 days used in the 30-day bracket.';
COMMENT ON COLUMN analysis.options_vol_index.dte_near        IS 'Calendar days from date to near_expiry_date.';
COMMENT ON COLUMN analysis.options_vol_index.dte_far         IS 'Calendar days from date to far_expiry_date.';
COMMENT ON COLUMN analysis.options_vol_index.var_near        IS 'Model-free variance (decimal) of the near expiry.';
COMMENT ON COLUMN analysis.options_vol_index.var_far         IS 'Model-free variance (decimal) of the far expiry.';
COMMENT ON COLUMN analysis.options_vol_index.variance_30d    IS 'Time-interpolated constant-30-day variance; equals the single nearest expiry variance when no bracket exists (early listings).';
COMMENT ON COLUMN analysis.options_vol_index.vol_index_30d   IS '100 * sqrt(variance_30d) — the index level in vol points (percent), directly comparable to VIX.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_vol_index', 'options_vol_index', NULL, NOW(),
     'Daily 30-day model-free implied-volatility index per underlying (CBOE VIX methodology adapted to settlement prices, r=0.02, calendar-day T): OTM-strip variance replication per expiry, time-interpolated to a constant 30-day maturity, vol_index_30d = 100*sqrt(variance_30d) in vol points. PK (underlying_code, date), no FK (date-granular). Built by analysis.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
