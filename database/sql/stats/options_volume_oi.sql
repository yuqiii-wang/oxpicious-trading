
-- ----------------------------------------------------------------------------
-- Table: options_volume_oi
--   ← Volume & open interest (contracts)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each option's daily traded volume and end-of-day open interest.
-- tags: options, 期权, volume, 成交量, open interest, 持仓量

CREATE TABLE IF NOT EXISTS stats.options_volume_oi (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_volume_oi_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    volume                    NUMERIC(24,4) NOT NULL DEFAULT 0,
    volume_wan                NUMERIC(24,4) NOT NULL DEFAULT 0,
    open_interest             NUMERIC(24,4) NOT NULL DEFAULT 0,
    open_interest_wan         NUMERIC(24,4) NOT NULL DEFAULT 0,
    amount                    NUMERIC(24,4),

    CONSTRAINT pk_options_volume_oi PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_volume_oi_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_volume_oi', 8);

COMMENT ON TABLE  stats.options_volume_oi             IS 'Options volume & open interest (contracts).';
COMMENT ON COLUMN stats.options_volume_oi.open_interest IS 'Open interest at end of day (contracts). Frontend uses this for call/put walls and max-pain. SSE carries the accumulated-volume ESTIMATOR (see builds.options.sse chain_open_interest), not published OI.';
COMMENT ON COLUMN stats.options_volume_oi.amount      IS 'Day traded value in yuan (成交额). SSE: EOD snapshot from the exchange quote page (temps/sse_options_daily). SZSE/CFFEX publish no per-contract amount: NULL.';

CREATE INDEX IF NOT EXISTS idx_options_volume_oi_date
    ON stats.options_volume_oi (date);

SELECT public.ensure_check_constraint('stats.options_volume_oi',  'chk_options_volume_oi_exchange',  'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
