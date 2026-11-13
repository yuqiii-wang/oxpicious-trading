
-- ----------------------------------------------------------------------------
-- Table: options_strike
--   ← Strike price data (in 厘 = 1/1000 yuan)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each option's strike price.
-- tags: options, 期权, strike, 行权价

CREATE TABLE IF NOT EXISTS stats.options_strike (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_strike_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    strike_str                TEXT,
    strike_price_raw          NUMERIC(18,4),
    strike_price              NUMERIC(18,4) NOT NULL DEFAULT 0,
    has_a_suffix              SMALLINT      NOT NULL DEFAULT 0
        CHECK (has_a_suffix IN (0,1)),

    CONSTRAINT pk_options_strike PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_strike_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_strike', 8);

COMMENT ON TABLE  stats.options_strike                IS 'Options strike price data (in 厘 = 1/1000 yuan).';
COMMENT ON COLUMN stats.options_strike.has_a_suffix   IS '1 if contract name carries "A" suffix (=contract adjusted for underlying split/dividend); 0 otherwise.';
COMMENT ON COLUMN stats.options_strike.strike_price   IS 'Normalized strike in 厘 (1/1000 yuan). Divide by 1000 for yuan.';

CREATE INDEX IF NOT EXISTS idx_options_strike_date
    ON stats.options_strike (date);

SELECT public.ensure_check_constraint('stats.options_strike',     'chk_options_strike_exchange',     'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
