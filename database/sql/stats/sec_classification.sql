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
--  catalog now lives in database/sec_statics/sec_classification.json (maintained by
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
--    'etf'    — ETF rows (code stored bare, e.g. 510050)
--    'index'  — Index rows (code stored bare, e.g. 000300)
--    'stock'  — Stock rows (code stored WITH exchange suffix, e.g. 000001.SZ)
--
--  The `exchange` column captures the listing board / market:
--    SZ, SS, GEM (创业板), STAR (科创板), BJ, HK, OVERSEAS (non-Greater-China)
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Sec Classification — one row per security (ETF / index / stock)
--  Label/slug columns are denormalized so callers don't JOIN a catalog.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The unified classification of every ETF, index and stock — its sector, industry, listing board and quality flags, denormalized for direct reads.
-- tags: classification, 分类, sector, 板块, industry, 行业, identity, 标识, reference, 参考

CREATE TABLE IF NOT EXISTS stats.sec_classification (
    code                      TEXT          NOT NULL,
    name                      TEXT          NOT NULL DEFAULT '',
    type                      TEXT          NOT NULL DEFAULT '',
    exchange                  TEXT,
    is_primary_exchange       BOOLEAN,

    sector_id                 TEXT          NOT NULL DEFAULT 'OTHER',
    sector_label              TEXT          NOT NULL DEFAULT '其他',
    industry_id               TEXT          NOT NULL DEFAULT 'OTHER',
    industry_label            TEXT          NOT NULL DEFAULT '未分类',
    industry_slug             TEXT          NOT NULL DEFAULT 'other',

    -- is_industry_not_strategy: when TRUE, sector_id/industry_id hold the
    -- INDUSTRY classification (FIN/BANKS, TECH/SEMI, …).  When FALSE, they
    -- hold the STRATEGY classification (BROAD/BROAD_CSI, DIV/DIV_SOE, …).
    -- The same five columns carry whichever classification is PRIMARY for
    -- this security; the flag tells the UI which dimension to display.
    is_industry_not_strategy  BOOLEAN       NOT NULL DEFAULT TRUE,

    first_date                DATE,
    last_date                 DATE,
    n_days                    INTEGER       NOT NULL DEFAULT 0,

    has_margin                BOOLEAN       NOT NULL DEFAULT FALSE,
    avg_shares                NUMERIC(24,4) NOT NULL DEFAULT 0,

    parent_index_code         TEXT          NOT NULL DEFAULT '',
    parent_index_weight       NUMERIC(8,4),
    parent_index_is_primary   BOOLEAN       NOT NULL DEFAULT FALSE,

    aum_yi                    NUMERIC(12,4),
    owner_id                  TEXT,
    is_dummy                  BOOLEAN       NOT NULL DEFAULT FALSE,
    is_active                 BOOLEAN       NOT NULL DEFAULT TRUE,

    CONSTRAINT pk_sec_classification PRIMARY KEY (code, parent_index_code),
    CONSTRAINT chk_sec_classification_type CHECK (type IN ('etf', 'index', 'stock')),
    CONSTRAINT chk_sec_classification_exchange CHECK (exchange IN ('SZ', 'SS', 'GEM', 'STAR', 'BJ', 'HK', 'OVERSEAS'))
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'sec_classification', 8);


COMMENT ON TABLE  stats.sec_classification                       IS 'Unified security metadata: ETF + Index + Stock classification + quality metrics. Labels are denormalized so callers need no JOIN to a catalog table. Replaces former etf_meta + index_meta + etf_index_map + stock_industry_map + sec_sector_industry_map.';
COMMENT ON COLUMN stats.sec_classification.code                  IS 'Security code. ETF: bare 6-digit (510050). Stock: WITH exchange suffix (000001.SZ). Index: bare 6-digit (000300).';
COMMENT ON COLUMN stats.sec_classification.type                  IS 'Security type: etf, index, or stock.';
COMMENT ON COLUMN stats.sec_classification.exchange              IS 'Listing board/market: SZ, SS, GEM (创业板), STAR (科创板), BJ, HK (港股通/港交所), OVERSEAS (non-Greater-China QDII targets — US/Japan/Europe/emerging). NULL for synthetic DUMMY indices. Indices use bare 6-digit codes with exchange derived from code prefix (000/930/931/932/950/990→SS, 399→SZ, 899→BJ, H/CES→HK) + name/sector overrides (HK-themed names→HK, OVERSEAS sector→OVERSEAS). ETFs inherit from the exchange column (CSV 上市地), overridden to HK/OVERSEAS when the underlying market is HK/non-Greater-China (name keywords: 标普/纳斯达克/美国/日经/德国/法国/欧洲/巴西/沙特/印度/越南/亚太精选 → OVERSEAS; 港股通/恒生/沪港深/SHS → HK). Stocks use code suffix only (.SZ/.SS/.BJ) — A-shares held by HK/overseas-themed indices keep their own exchange.';
COMMENT ON COLUMN stats.sec_classification.is_primary_exchange  IS 'Derived flag — ALWAYS generated by the rule (post-upsert UPDATE in build_classification sector_industry/upsert.py, same pattern as is_active): TRUE for Greater-China primary exchanges (SS, STAR, SZ, GEM, BJ); FALSE for cross-border / non-Greater-China (HK, OVERSEAS); NULL when exchange IS NULL (synthetic DUMMY indices). Splits the UI exchange filter into two rows: primary (SSE/SZSE/BSE) and cross-border (HK/Overseas). Never set manually — re-derived from `exchange` every build.';
COMMENT ON COLUMN stats.sec_classification.sector_id             IS 'L1 classification id. When is_industry_not_strategy=TRUE: industry sector (FIN, TECH, HC, …). When FALSE: strategy id (BROAD, DIV, REGION, STRATEGY, SOE, OVERSEAS). Default OTHER.';
COMMENT ON COLUMN stats.sec_classification.sector_label          IS 'L1 label (denormalized). Chinese display string, e.g. 金融, 科技, 宽基, 红利.';
COMMENT ON COLUMN stats.sec_classification.industry_id           IS 'L2 classification id. When is_industry_not_strategy=TRUE: industry id (BANKS, SEMI, …). When FALSE: theme id (BROAD_CSI, DIV_SOE, …). Default OTHER.';
COMMENT ON COLUMN stats.sec_classification.industry_label        IS 'L2 label (denormalized). Chinese display string, e.g. 银行, 半导体, 中证, 央企/国企红利.';
COMMENT ON COLUMN stats.sec_classification.industry_slug         IS 'URL-safe slug = LOWER(industry_id), e.g. banks, semi, broad_csi, div_soe. Denormalized from catalog.';
COMMENT ON COLUMN stats.sec_classification.is_industry_not_strategy IS 'TRUE → sector_id/industry_id hold INDUSTRY classification (LEFT column). FALSE → they hold STRATEGY classification (RIGHT column). UI uses this flag to split display into two columns: left=industry, right=strategy.';
COMMENT ON COLUMN stats.sec_classification.n_days                IS 'Coverage: n_ohlcv_days for ETF, COUNT(*) for index/stock from identity table.';
COMMENT ON COLUMN stats.sec_classification.has_margin            IS 'ETF only: TRUE if any margin data exists. Populated by build_szse_sse_etf_and_margin.py.';
COMMENT ON COLUMN stats.sec_classification.avg_shares           IS 'ETF only: average daily volume in shares (converted × 10000 from source 万股). Populated by build_szse_sse_etf_and_margin.py.';
COMMENT ON COLUMN stats.sec_classification.parent_index_code     IS 'Hierarchy: ETF → tracking index code; stock → ONE ROW PER qualifying index (weight >2%, non-BROAD); index → empty string. PK component, never NULL.';
COMMENT ON COLUMN stats.sec_classification.parent_index_weight   IS 'Stock only: weight_pct of the stock in its parent index. NULL for ETF/index.';
COMMENT ON COLUMN stats.sec_classification.parent_index_is_primary IS 'TRUE for the single authoritative parent of a security. Stock: row with MAX(parent_index_weight) per code. ETF: TRUE iff ETF name (issuer/manager prefix + legal suffix stripped) exactly matches parent index name. Index: always FALSE (root of hierarchy).';
COMMENT ON COLUMN stats.sec_classification.aum_yi                IS 'ETF only: net asset value (AUM) in 亿元 (100M yuan). Populated by build_classification.py from etf_index_map_all_*.csv 资产净值（亿元） column. NULL for index/stock.';
COMMENT ON COLUMN stats.sec_classification.owner_id             IS 'Logical reference to stats.sec_owners.owner_id. ETF: matched fund manager (issuer). Stock: company name (NULL when no curated owner exists). Index: NULL. Populated by build_classification.py from database/sec_statics/sec_owners.json.';
COMMENT ON COLUMN stats.sec_classification.is_dummy             IS 'TRUE for synthetic industry dummy indices (type=''index'', code like DUMMY_BANKS). Created one per industry_id to serve as parent_index_code for orphan ETFs (ETFs with no CSV-mapped tracking index). FALSE for all real indices, ETFs, and stocks.';
COMMENT ON COLUMN stats.sec_classification.is_active            IS 'TRUE iff the security has >=1 record in the last year (trailing 365 days) in its identity table: index→stats.index_identity (bare 6-digit code), stock→stats.stock_identity (.SZ/.SS suffix), etf→stats.etf_identity (bare 6-digit code). FALSE for delisted ETFs, dead indices, and old stocks with no recent data. Dummy indices (is_dummy=TRUE) are always TRUE — they are synthetic parents for orphan ETFs and have no identity records of their own. Populated by build_classification (sector_industry/upsert.py) as a post-upsert SQL UPDATE.';

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

-- Partial covering index for the classification meta rows read by the
-- data_viz nav trees (see data_viz/api/services/classification-cache.ts):
-- one DISTINCT ON (code) row per code filtered by type + is_active.
CREATE INDEX IF NOT EXISTS idx_sec_classification_type_active
    ON stats.sec_classification (type, code)
    WHERE is_active = TRUE;
