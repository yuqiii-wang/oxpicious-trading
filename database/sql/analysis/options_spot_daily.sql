-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each option underlying's daily close as recorded by the options loader.
-- tags: options, 期权, underlying close, 标的价格, spot, 现货


CREATE TABLE IF NOT EXISTS analysis.options_spot_daily (
    date                      DATE          NOT NULL,
    underlying_code           TEXT          NOT NULL,
    close                     NUMERIC(18,4),

    CONSTRAINT pk_options_spot_daily
        PRIMARY KEY (underlying_code, date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by code — via the shared util
SELECT public.create_hash_partitions('analysis', 'options_spot_daily', 8);

COMMENT ON TABLE  analysis.options_spot_daily IS 'Per (underlying_code, date) underlying close from the options loader (MAX(stats.options_settlement.underlying_close) over the date''s settlement rows; NULL when every row is NULL, 0 stays 0 — the API maps close <= 0 to null). Serves the spot axis of /api/szse-options/oi-moneyness-series (every trading date the loader has settlement rows for, including dates with no valid moneyness group). Native underlying codes; no FK (date-granular). Built by analysis.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_spot_daily.close IS 'MAX(underlying_close) over the date''s settlement rows, in the loader''s raw units (SSE/SZSE ETF closes in 厘; CFFEX index closes in native points).';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_moneyness_series', 'options_moneyness_series', NULL, NOW(),
     'Materialized oi_moneyness skew series per (underlying_code, date, expiry_month): per month group and date — n_valid (valid rows: expiry_date >= date AND 0 < implied_vol < 5), wsum_k/wsum (GREATEST(OI,1)-weighted strike and weight sums), e_m = (Σ w·K / Σ w) / day close (NULL when n_valid < 3 or close missing/<= 0), call/put OI and OTM OI sums over the group''s active rows, expiry_date = MIN valid expiry. The precomputed read model behind the OI-weighted Moneyness Skew panel''s /oi-moneyness-series endpoint (replaces the former live 5-way full-history join, ~4.8s cold on the biggest CFFEX underlying). PK (underlying_code, date, expiry_month), hash-partitioned by underlying_code; no FK (month-group keys). Built by analysis.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_spot_daily', 'options_spot_daily', NULL, NOW(),
     'Per (underlying_code, date) underlying close from the options loader (MAX(options_settlement.underlying_close) over the date''s settlement rows; close <= 0 is mapped to null by the API). Serves the spot axis of /oi-moneyness-series (dates with no valid moneyness group included). PK (underlying_code, date), hash-partitioned by underlying_code; no FK (date-granular). Built by analysis.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
