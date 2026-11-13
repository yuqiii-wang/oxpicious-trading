
-- ----------------------------------------------------------------------------
-- Table: options_terms
--   ← Underlying and contract terms
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each option's contract terms — its underlying, option type, expiry month and expiry date.
-- tags: options, 期权, contract terms, 合约条款, expiry, 到期日, underlying, 标的

CREATE TABLE IF NOT EXISTS stats.options_terms (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    underlying_code           TEXT          NOT NULL,
    underlying_name           TEXT          NOT NULL,
    underlying_target_type      TEXT          NOT NULL
        CHECK (underlying_target_type IN ('ETF','INDEX')),
    exchange                  TEXT          NOT NULL
        CHECK (exchange IN ('SZSE','SSE','CFFEX')),

    option_type               SMALLINT      NOT NULL,  -- label code (static.label_dim 'option_type'): 0=CALL 1=PUT
    expiry_month              TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,
    days_to_expiry            INTEGER       NOT NULL DEFAULT 0,

    CONSTRAINT pk_options_terms PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_terms_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_terms', 8);

COMMENT ON TABLE  stats.options_terms                 IS 'Options underlying and contract terms.';
COMMENT ON COLUMN stats.options_terms.underlying_code IS 'SZSE: native ETF code (e.g. "159901"). CFFEX: underlying index code (e.g. "000300"). Venues are separated by code space + underlying_target_type.';
COMMENT ON COLUMN stats.options_terms.option_type     IS 'Option type as its static.label_dim (''option_type'' domain) SMALLINT code: 0 = CALL (认购, right to buy); 1 = PUT (认沽, right to sell).';
COMMENT ON COLUMN stats.options_terms.expiry_month    IS 'Chinese month label from contract name (e.g. "12月"); for display only — use expiry_date for date math.';


-- Indexes
-- Underlying-code lookups on options_terms (non-PK key path).
CREATE INDEX IF NOT EXISTS idx_options_quote_underlying_date
    ON stats.options_terms (underlying_code, date);


CREATE INDEX IF NOT EXISTS idx_options_quote_underlying_date_expiry
    ON stats.options_terms (underlying_code, date, expiry_date);
