-- ============================================================================
--  Options Quote - Split Tables
--  Original: options_quote table from schema.sql
--  Split into: options_identity, options_terms, options_strike, options_settlement,
--              options_greeks, options_volume_oi, options_aggregate
--  Reconstruct via: v_options_quote view (see 99_reconstruct_views.sql)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: options_identity
--   Identity core (PK) for all options_quote sub-tables
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Which option contract traded on which day — the shared identity for all options daily tables.
-- tags: options, 期权, identity, 标识, reference, 参考

CREATE TABLE IF NOT EXISTS stats.options_identity (
    date                      DATE          NOT NULL,
    contract_code             TEXT          NOT NULL,
    contract_name             TEXT          NOT NULL,
    exchange                  TEXT          NOT NULL
        CONSTRAINT chk_options_identity_exchange
        CHECK (exchange IN ('SSE','SZSE','CFFEX')),

    CONSTRAINT pk_options_identity PRIMARY KEY (contract_code, date)
) PARTITION BY HASH (contract_code);

-- Native hash partitions (8) keyed by contract_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'options_identity', 8);

COMMENT ON TABLE  stats.options_identity              IS 'Options identity: one row per (date, contract_code). PK (contract_code, date) shared by all options sub-tables. Native HASH partitioned by contract_code.';
COMMENT ON COLUMN stats.options_identity.contract_code IS 'SZSE option contract code (8-digit numeric string).';


COMMENT ON COLUMN stats.options_identity.exchange IS 'Venue of the contract: SSE / SZSE / CFFEX. Same value as options_terms.exchange, denormalized onto every options_* table so venue filters need no join.';


-- The legacy (contract_code, date) secondary indexes are superseded by
-- the code-first PK; the date-first indexes below restore the
-- cross-contract date scans the old date-first PK used to serve.

CREATE INDEX IF NOT EXISTS idx_options_identity_date
    ON stats.options_identity (date);


-- CHECK constraints via the shared guarded helper (NOT VALID, then validated
-- schema-wide below) — naming must keep the chk_ prefix for the validator.
SELECT public.ensure_check_constraint('stats.options_identity',   'chk_options_identity_exchange',   'exchange IN (''SSE'',''SZSE'',''CFFEX'')');
