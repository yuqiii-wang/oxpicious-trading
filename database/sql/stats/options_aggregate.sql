
-- ----------------------------------------------------------------------------
-- Table: options_aggregate
--   ← Per-underlying aggregate context
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each option underlying's daily whole-chain aggregate context.
-- tags: options, 期权, aggregate, 汇总, underlying, 标的

CREATE TABLE IF NOT EXISTS stats.options_aggregate (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_aggregate_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),
    total_volume_underlying   NUMERIC(24,4),
    total_oi_underlying       NUMERIC(24,4),
    volume_pct                NUMERIC(10,6),
    open_interest_pct         NUMERIC(10,6),
    oi_call_put_ratio         NUMERIC(18,8),
    vol_call_put_ratio        NUMERIC(18,8),
    open_interest_call        NUMERIC(24,4),
    open_interest_put         NUMERIC(24,4),
    volume_call               NUMERIC(24,4),
    volume_put                NUMERIC(24,4),
    oi_total_call_put_ratio   NUMERIC(18,8),

    CONSTRAINT pk_options_aggregate PRIMARY KEY (contract_code, date),
    CONSTRAINT fk_options_aggregate_date_contract FOREIGN KEY (contract_code, date) REFERENCES stats.options_identity(contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_aggregate', 8);

COMMENT ON TABLE  stats.options_aggregate            IS 'Options per-underlying aggregate context (one row per (underlying_code, date)).';
CREATE INDEX IF NOT EXISTS idx_options_aggregate_date
    ON stats.options_aggregate (date);
SELECT public.ensure_check_constraint('stats.options_aggregate',  'chk_options_aggregate_exchange',  'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
SELECT public.validate_pending_checks('stats');
