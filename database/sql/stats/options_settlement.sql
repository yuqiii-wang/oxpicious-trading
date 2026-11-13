
-- ----------------------------------------------------------------------------
-- Table: options_settlement
--   ← Daily settlement prices and moneyness
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each option's daily settlement price together with its underlying's close that day.
-- tags: options, 期权, settlement, 结算价, underlying close, 标的价格

CREATE TABLE IF NOT EXISTS stats.options_settlement (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_settlement_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    prev_settle               NUMERIC(18,4),
    close                     NUMERIC(18,4),
    settle                    NUMERIC(18,4),
    pct_change                NUMERIC(10,4),
    prev_settle_norm          NUMERIC(18,6),
    close_norm                NUMERIC(18,6),
    settle_norm               NUMERIC(18,6),
    underlying_close          NUMERIC(18,4) NOT NULL DEFAULT 0,
    moneyness_ratio           NUMERIC(12,8),

    CONSTRAINT pk_options_settlement PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_settlement_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_settlement', 8);

COMMENT ON TABLE  stats.options_settlement            IS 'Options daily settlement prices and moneyness.';
COMMENT ON COLUMN stats.options_settlement.settle    IS 'Daily settlement price in 元/张 (yuan per contract).';

CREATE INDEX IF NOT EXISTS idx_options_settlement_date
    ON stats.options_settlement (date);

SELECT public.ensure_check_constraint('stats.options_settlement', 'chk_options_settlement_exchange', 'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
