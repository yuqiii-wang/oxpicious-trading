
-- ----------------------------------------------------------------------------
-- Table: options_greeks
--   ← Greeks (per-contract, Black-Scholes model)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each option's per-day risk greeks — delta, gamma, vega, theta, rho — and its implied volatility.
-- tags: options, 期权, greeks, 希腊字母, implied volatility, 隐含波动率

CREATE TABLE IF NOT EXISTS stats.options_greeks (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_greeks_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    implied_vol               NUMERIC(12,8),
    delta                     NUMERIC(18,8),
    theta                     NUMERIC(18,8),
    gamma                     NUMERIC(18,8),
    vega                      NUMERIC(18,8),
    rho                       NUMERIC(18,8),

    CONSTRAINT pk_options_greeks PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_greeks_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_greeks', 8);

COMMENT ON TABLE  stats.options_greeks                IS 'Options Greeks (per-contract, Black-Scholes model).';
COMMENT ON COLUMN stats.options_greeks.implied_vol   IS 'Black-Scholes implied vol (decimal, not %). NULL if computation failed.';

CREATE INDEX IF NOT EXISTS idx_options_greeks_date
    ON stats.options_greeks (date);

SELECT public.ensure_check_constraint('stats.options_greeks',     'chk_options_greeks_exchange',     'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
