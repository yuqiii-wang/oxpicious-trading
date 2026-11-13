-- ============================================================================
--  Index Daily - Split Tables
--  Original: CSIndex daily history + intraday tick data
--  Split into: index_identity, index_basic_stats, index_valuation,
--              index_tech_stats, index_intraday_5min
--  Reconstruct via: v_index_daily view (see 99_reconstruct_views.sql)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: index_identity
--   Identity core (PK) for all index sub-tables
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Which index traded on which day — the shared identity for all index daily tables.
-- tags: index, 指数, identity, 标识, reference, 参考

CREATE TABLE IF NOT EXISTS stats.index_identity (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    name                      TEXT          NOT NULL,

    CONSTRAINT pk_index_identity PRIMARY KEY (code, date),
    CONSTRAINT chk_index_identity_code_format
        CHECK (code ~ '^(\d{6}|H\d{5}|[A-Z][A-Z0-9]{3,11})$')
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'index_identity', 8);

COMMENT ON TABLE  stats.index_identity                 IS 'Index identity: one row per (date, index_code). PK (code, date) shared by all index sub-tables. Native HASH partitioned by code.';
COMMENT ON COLUMN stats.index_identity.code            IS 'Index code: onshore "000300" (CSI300), "H30007" (chip industry); offshore uppercase-alnum e.g. "MICN0A5C0PCY" (MSCI China A 50 Connect, HKEX marquee RIC).';


-- Indexes

-- (a) Per-code lookups (latest first); INCLUDE (name) lets the
--     Index Only Scan return the name without heap fetches.
--     (Plain (code, date) prefix is now served by the PK itself.)
CREATE INDEX IF NOT EXISTS idx_index_identity_code_date
    ON stats.index_identity (code, date DESC) INCLUDE (name);


-- Date-first scans across all codes (the old date-first PK served these;
-- PK is now (code, date) so an explicit date index restores the pattern).
CREATE INDEX IF NOT EXISTS idx_index_identity_date
    ON stats.index_identity (date);
