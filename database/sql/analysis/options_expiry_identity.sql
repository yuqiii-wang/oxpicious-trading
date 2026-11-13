-- ============================================================================
--  Options Expiry Identity — lookup table for (date, option_type,
--  underlying_code, expiry_date) groups. All other options analysis
--  tables have FK references to this table.
--
--  Table: analysis.options_expiry_identity
--    PK: (date, option_type, underlying_code, expiry_date)
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The collapsed expiry-group identity for the options analysis tables — one row per underlying, day, option type and expiry group.
-- tags: options, 期权, expiry, 到期, identity, 标识, underlying, 标的

CREATE TABLE IF NOT EXISTS analysis.options_expiry_identity (
    date                      DATE          NOT NULL,
    option_type               SMALLINT      NOT NULL,  -- label code (static.label_dim 'option_type'): 0=CALL 1=PUT
    underlying_code           TEXT          NOT NULL,
    expiry_date               DATE          NOT NULL,

    CONSTRAINT pk_options_expiry_identity
        PRIMARY KEY (underlying_code, date, option_type, expiry_date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by underlying_code
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_expiry_identity', 8);
