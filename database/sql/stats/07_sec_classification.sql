-- ============================================================================
--  Sec Classification — unified security metadata + two-level classification
--  for ETF + Index + Stock.
--
--  Replaces the former stats.stock_industry_map (see 09_sec_map.sql) and
--  consolidates the former etf_meta / index_meta / etf_index_map concepts
--  into a single table keyed by code with a `type` discriminator.
--
--  Source: build_classification.py (classification + parent_index mapping),
--          build_szse_sse_etf_and_margin.py (ETF quality metrics).
--
--  Two-level classification model:
--    L1 sector   — broad theme (FIN, TECH, HC, ENG, NEV, BROAD, ...)
--    L2 industry — narrow theme (BANKS, SEMI, PHARMA_BROAD, OIL, PV, ...)
--
--  Industries are unique identifiers but MAY appear under multiple sectors
--  (overlapping sectors, e.g. POWER_EQUIP can be both ENG and IND).
--  Each security is assigned exactly ONE (sector_id, industry_id) pair.
--
--  The label/slug columns (sector_label, industry_label, industry_slug) are
--  DENORMALIZED directly onto sec_classification so callers can render labels
--  without a JOIN to a separate catalog table. The former
--  stats.sec_sector_industry_map catalog has been DROPPED — the authoritative
--  catalog now lives in sec_classification.json (maintained by
--  build_classification.py from INDEX_RULES + OVERLAPPING_CATALOG).
--
--  Hierarchy via parent_index_code / parent_index_weight:
--    index — parent_index_code = '' (empty string, root of hierarchy)
--    etf   — parent_index_code = tracking index code (one-to-one from CSV)
--    stock — ONE ROW PER qualifying index (weight > 2%, excluding BROAD
--            sector indices) from sec_composition. A stock may thus have
--            multiple rows. Stocks without any qualifying index get a
--            single row with parent_index_code = ''.
--
--  The `type` column distinguishes:
--    'etf'    — ETF rows (code stored WITH exchange suffix, e.g. 510050.SS)
--    'index'  — Index rows (code stored bare, e.g. 000300)
--    'stock'  — Stock rows (code stored WITH exchange suffix, e.g. 000001.SZ)
--
--  The `exchange` column captures the listing board / market:
--    SZ, SS, GEM (创业板), STAR (科创板), BJ, HK
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Sec Classification — one row per security (ETF / index / stock)
--  Label/slug columns are denormalized so callers don't JOIN a catalog.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.sec_classification (
    code                      TEXT          NOT NULL,
    name                      TEXT          NOT NULL DEFAULT '',
    type                      TEXT          NOT NULL DEFAULT '',
    exchange                  TEXT,

    sector_id                 TEXT          NOT NULL DEFAULT 'OTHER',
    sector_label              TEXT          NOT NULL DEFAULT '其他',
    industry_id               TEXT          NOT NULL DEFAULT 'OTHER',
    industry_label            TEXT          NOT NULL DEFAULT '未分类',
    industry_slug             TEXT          NOT NULL DEFAULT 'other',

    first_date                DATE,
    last_date                 DATE,
    n_days                    INTEGER       NOT NULL DEFAULT 0,

    has_margin                BOOLEAN       NOT NULL DEFAULT FALSE,
    avg_shares                NUMERIC(24,4) NOT NULL DEFAULT 0,
    selectivity_rank_score    INTEGER       NOT NULL DEFAULT 0,

    parent_index_code         TEXT          NOT NULL DEFAULT '',
    parent_index_weight       NUMERIC(8,4),
    parent_index_is_primary   BOOLEAN       NOT NULL DEFAULT FALSE,

    aum_yi                    NUMERIC(12,4),
    owner_id                  TEXT,

    CONSTRAINT pk_sec_classification PRIMARY KEY (code, parent_index_code),
    CONSTRAINT chk_sec_classification_type CHECK (type IN ('etf', 'index', 'stock')),
    CONSTRAINT chk_sec_classification_exchange CHECK (exchange IN ('SZ', 'SS', 'GEM', 'STAR', 'BJ', 'HK'))
);

-- Add aum_yi / owner_id / parent_index_is_primary columns to existing tables
-- (no-op if already present). Needed because CREATE TABLE IF NOT EXISTS does
-- not add new columns to an existing table — each column was added to the DDL
-- after the table was first created, so the live table lacks them until these
-- ALTERs run.  MUST run before the COMMENT ON COLUMN statements below, which
-- reference these columns (COMMENT fails if the column does not exist).
ALTER TABLE stats.sec_classification ADD COLUMN IF NOT EXISTS aum_yi NUMERIC(12,4);
ALTER TABLE stats.sec_classification ADD COLUMN IF NOT EXISTS parent_index_is_primary BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE stats.sec_classification ADD COLUMN IF NOT EXISTS owner_id TEXT;


COMMENT ON TABLE  stats.sec_classification                       IS 'Unified security metadata: ETF + Index + Stock classification + quality metrics. Labels are denormalized so callers need no JOIN to a catalog table. Replaces former etf_meta + index_meta + etf_index_map + stock_industry_map + sec_sector_industry_map.';
COMMENT ON COLUMN stats.sec_classification.code                  IS 'Security code. ETF/Stock: WITH exchange suffix (510050.SS, 000001.SZ). Index: bare 6-digit (000300).';
COMMENT ON COLUMN stats.sec_classification.type                  IS 'Security type: etf, index, or stock.';
COMMENT ON COLUMN stats.sec_classification.exchange              IS 'Listing board/market: SZ, SS, GEM (创业板), STAR (科创板), BJ, HK. NULL for cross-market indices.';
COMMENT ON COLUMN stats.sec_classification.sector_id             IS 'L1 sector id (e.g. FIN, TECH, HC, BROAD, ENG, NEV). Default OTHER.';
COMMENT ON COLUMN stats.sec_classification.sector_label          IS 'L1 sector label (denormalized from catalog). Chinese display string, e.g. 金融, 科技, 宽基.';
COMMENT ON COLUMN stats.sec_classification.industry_id           IS 'L2 industry id (e.g. BANKS, SEMI, BROAD_CSI). Default OTHER.';
COMMENT ON COLUMN stats.sec_classification.industry_label        IS 'L2 industry label (denormalized from catalog). Chinese display string, e.g. 银行, 半导体, 中证.';
COMMENT ON COLUMN stats.sec_classification.industry_slug         IS 'URL-safe slug = LOWER(industry_id), e.g. banks, semi, broad_csi. Denormalized from catalog.';
COMMENT ON COLUMN stats.sec_classification.n_days                IS 'Coverage: n_ohlcv_days for ETF, COUNT(*) for index/stock from identity table.';
COMMENT ON COLUMN stats.sec_classification.has_margin            IS 'ETF only: TRUE if any margin data exists. Populated by build_szse_sse_etf_and_margin.py.';
COMMENT ON COLUMN stats.sec_classification.avg_shares           IS 'ETF only: average daily volume in shares (converted × 10000 from source 万股). Populated by build_szse_sse_etf_and_margin.py.';
COMMENT ON COLUMN stats.sec_classification.selectivity_rank_score IS 'ETF only: composite score. Populated by build_szse_sse_etf_and_margin.py.';
COMMENT ON COLUMN stats.sec_classification.parent_index_code     IS 'Hierarchy: ETF → tracking index code; stock → ONE ROW PER qualifying index (weight >2%, non-BROAD); index → empty string. PK component, never NULL.';
COMMENT ON COLUMN stats.sec_classification.parent_index_weight   IS 'Stock only: weight_pct of the stock in its parent index. NULL for ETF/index.';
COMMENT ON COLUMN stats.sec_classification.parent_index_is_primary IS 'TRUE for the single authoritative parent of a security. Stock: row with MAX(parent_index_weight) per code. ETF: TRUE iff ETF name (issuer/manager prefix + legal suffix stripped) exactly matches parent index name. Index: always FALSE (root of hierarchy).';
COMMENT ON COLUMN stats.sec_classification.aum_yi                IS 'ETF only: net asset value (AUM) in 亿元 (100M yuan). Populated by build_classification.py from etf_index_map_all_*.csv 资产净值（亿元） column. NULL for index/stock.';
COMMENT ON COLUMN stats.sec_classification.owner_id             IS 'Logical reference to stats.sec_owners.owner_id. ETF: matched fund manager (issuer). Stock: company name (NULL when no curated owner exists). Index: NULL. Populated by build_classification.py from sec_owners.json.';

CREATE INDEX IF NOT EXISTS idx_sec_classification_type
    ON stats.sec_classification (type);

CREATE INDEX IF NOT EXISTS idx_sec_classification_sector_industry
    ON stats.sec_classification (sector_id, industry_id);

CREATE INDEX IF NOT EXISTS idx_sec_classification_parent_index
    ON stats.sec_classification (parent_index_code)
    WHERE parent_index_code <> '';

CREATE INDEX IF NOT EXISTS idx_sec_classification_exchange
    ON stats.sec_classification (exchange)
    WHERE exchange IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sec_classification_primary_parent
    ON stats.sec_classification (code)
    WHERE parent_index_is_primary = TRUE;

CREATE INDEX IF NOT EXISTS idx_sec_classification_owner
    ON stats.sec_classification (owner_id)
    WHERE owner_id IS NOT NULL;

-- ----------------------------------------------------------------------------
--  Sec Index Tags — multi-classification for indices
--  An index may carry MULTIPLE (sector_id, industry_id) tags, enabling
--  multi-faceted browsing (e.g. "央企红利" is both DIV/DIV_SOE and
--  BROAD/BROAD_SOE).  The PRIMARY classification remains in
--  sec_classification (sector_id/industry_id columns); this table stores
--  the full set of tags for filtering/grouping.
--
--  Populated by build_classification.py from sec_classification.json `tags`
--  arrays.  Truncated + rebuilt every run.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.sec_index_tags (
    code                      TEXT          NOT NULL,
    sector_id                 TEXT          NOT NULL,
    industry_id               TEXT          NOT NULL,
    is_broad_market           BOOL,
    CONSTRAINT pk_sec_index_tags PRIMARY KEY (code, sector_id, industry_id)
);

COMMENT ON TABLE  stats.sec_index_tags              IS 'Multi-classification tags for indices. Each index can have multiple (sector_id, industry_id) pairs. Primary tag matches sec_classification.sector_id/industry_id.';
COMMENT ON COLUMN stats.sec_index_tags.code         IS 'Index code (bare 6-digit, e.g. 000300). References sec_classification.code where type=''index''.';
COMMENT ON COLUMN stats.sec_index_tags.sector_id    IS 'L1 sector id (e.g. DIV, MIL, AERO, BROAD, TECH).';
COMMENT ON COLUMN stats.sec_index_tags.industry_id  IS 'L2 industry id (e.g. DIV_SOE, MIL_DEFENSE, AERO_SPACE, BROAD_CSI).';
COMMENT ON COLUMN stats.sec_index_tags.is_broad_market IS 'TRUE iff this tag represents a broad-market classification (sector_id = ''BROAD''). An index is considered broad-market if ANY of its tags has is_broad_market=TRUE. Populated by build_classification.py from the tag sector_id.';

-- Add is_broad_market column to existing tables (no-op if already present).
-- Needed because CREATE TABLE IF NOT EXISTS does not add new columns to an
-- existing table — the column was added to the DDL after the table was first
-- created, so the live table lacks it until this ALTER runs.
ALTER TABLE stats.sec_index_tags ADD COLUMN IF NOT EXISTS is_broad_market BOOL;

CREATE INDEX IF NOT EXISTS idx_sec_index_tags_sector_industry
    ON stats.sec_index_tags (sector_id, industry_id);


-- ----------------------------------------------------------------------------
--  Sec Owners — registry of security owners
--  For ETFs the owner is the fund manager / issuer (e.g. 南方, 华夏, 国泰).
--  For stocks the owner is the listed company itself.  Indices have no owner.
--
--  Source: sec_owners.json (curated, hand-editable cache maintained alongside
--  build_classification.py).  Each entry carries one or more `aliases`
--  (short-name prefixes used to match the ETF name) and `full_names`
--  (legal entity names that match the CSV `管理人` column exactly).
--
--  stats.sec_classification.owner_id is a logical (non-FK) reference to this
--  table's `owner_id` column — populated by build_classification.py.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.sec_owners (
    owner_id                  TEXT          NOT NULL,
    name                      TEXT          NOT NULL,
    type                      TEXT,
    aliases                   TEXT[]        NOT NULL DEFAULT '{}',
    full_names                TEXT[]        NOT NULL DEFAULT '{}',

    CONSTRAINT pk_sec_owners PRIMARY KEY (owner_id),
    CONSTRAINT chk_sec_owners_type CHECK (type IN
        ('fund_manager', 'broker', 'asset_manager', 'insurance', 'bank', 'company', 'other'))
);

COMMENT ON TABLE  stats.sec_owners               IS 'Registry of security owners (ETF fund managers / issuers, stock companies). Source: sec_owners.json. Referenced logically by stats.sec_classification.owner_id.';
COMMENT ON COLUMN stats.sec_owners.owner_id      IS 'Stable identifier (slug). Used as the FK target by sec_classification.owner_id.';
COMMENT ON COLUMN stats.sec_owners.name          IS 'Display name (short form), e.g. 南方, 华夏, 国泰君安.';
COMMENT ON COLUMN stats.sec_owners.type          IS 'Owner type: fund_manager, broker, asset_manager, insurance, bank, company, other.';
COMMENT ON COLUMN stats.sec_owners.aliases       IS 'Short-name prefixes used to match ETF names (longest alias wins). e.g. ["南方"] matches "南方中证全指食品交易型...". Includes the name itself by convention.';
COMMENT ON COLUMN stats.sec_owners.full_names    IS 'Full legal entity names that match the etf_index_map CSV `管理人` column exactly, e.g. ["南方基金管理股份有限公司"].';

-- Add type / aliases / full_names columns to existing tables (no-op if already
-- present). Needed because CREATE TABLE IF NOT EXISTS does not add new columns
-- to an existing table — the columns were added to the DDL after the table was
-- first created, so the live table lacks them until these ALTERs run.
ALTER TABLE stats.sec_owners ADD COLUMN IF NOT EXISTS type       TEXT;
ALTER TABLE stats.sec_owners ADD COLUMN IF NOT EXISTS aliases    TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE stats.sec_owners ADD COLUMN IF NOT EXISTS full_names TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_sec_owners_type
    ON stats.sec_owners (type)
    WHERE type IS NOT NULL;

