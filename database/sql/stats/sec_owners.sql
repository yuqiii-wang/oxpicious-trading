

-- ----------------------------------------------------------------------------
--  Sec Owners — registry of security owners
--  For ETFs the owner is the fund manager / issuer (e.g. 南方, 华夏, 国泰).
--  For stocks the owner is the listed company itself.  Indices have no owner.
--
--  MIGRATED from 07_sec_classification.sql.  Now populated by builds.sec_info
--  (truncate + rebuild each run from sec_owners.json).
--
--  Source: database/sec_statics/sec_owners.json (curated, hand-editable cache
--  maintained alongside builds.sec_info).  Each entry carries one or more
--  `aliases` (short-name prefixes used to match the ETF name) and `full_names`
--  (legal entity names that match the CSV `管理人` column exactly).
--
--  stats.sec_classification.owner_id is a logical (non-FK) reference to this
--  table's `owner_id` column — populated in-memory by builds.classification
--  (load_owners + build_owner_matchers + match_etf_owner).  No DB join is
--  required at classification time; this table exists for UI/reference joins.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The registry of security owners — ETF fund managers and stock companies.
-- tags: owners, 管理人, fund managers, 基金管理人, reference, 参考

CREATE TABLE IF NOT EXISTS stats.sec_owners (
    owner_id                  TEXT          NOT NULL,
    name                      TEXT          NOT NULL,
    type                      SMALLINT,  -- label code (static.label_dim 'sec_owner_type'): 0=fund_manager 1=broker 2=asset_manager 3=insurance 4=bank 5=company 6=other
    aliases                   TEXT[]        NOT NULL DEFAULT '{}',
    full_names                TEXT[]        NOT NULL DEFAULT '{}',

    CONSTRAINT pk_sec_owners PRIMARY KEY (owner_id)
);

COMMENT ON TABLE  stats.sec_owners               IS 'Registry of security owners (ETF fund managers / issuers, stock companies). Migrated from 07_sec_classification.sql; populated by builds.sec_info (truncate + rebuild from database/sec_statics/sec_owners.json). Referenced logically (non-FK) by stats.sec_classification.owner_id.';
COMMENT ON COLUMN stats.sec_owners.owner_id      IS 'Stable identifier (slug). Used as the logical FK target by sec_classification.owner_id.';
COMMENT ON COLUMN stats.sec_owners.name          IS 'Display name (short form), e.g. 南方, 华夏, 国泰君安.';
COMMENT ON COLUMN stats.sec_owners.type          IS 'Owner type as its static.label_dim (''sec_owner_type'' domain) SMALLINT code: 0 = fund_manager, 1 = broker, 2 = asset_manager, 3 = insurance, 4 = bank, 5 = company, 6 = other. Mapped once at the write boundary from sec_owners.json; NULL when the source entry carries no type.';
COMMENT ON COLUMN stats.sec_owners.aliases       IS 'Short-name prefixes used to match ETF names (longest alias wins). e.g. ["南方"] matches "南方中证全指食品交易型...". Includes the name itself by convention.';
COMMENT ON COLUMN stats.sec_owners.full_names    IS 'Full legal entity names that match the etf_index_map CSV `管理人` column exactly, e.g. ["南方基金管理股份有限公司"].';

CREATE INDEX IF NOT EXISTS idx_sec_owners_type
    ON stats.sec_owners (type)
    WHERE type IS NOT NULL;
