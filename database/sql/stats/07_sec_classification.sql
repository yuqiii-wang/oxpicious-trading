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
--  catalog now lives in _common/sec_statics/sec_classification.json (maintained by
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
    selectivity_rank_score    INTEGER       NOT NULL DEFAULT 0,

    parent_index_code         TEXT          NOT NULL DEFAULT '',
    parent_index_weight       NUMERIC(8,4),
    parent_index_is_primary   BOOLEAN       NOT NULL DEFAULT FALSE,

    aum_yi                    NUMERIC(12,4),
    owner_id                  TEXT,
    is_dummy                  BOOLEAN       NOT NULL DEFAULT FALSE,
    is_active                 BOOLEAN       NOT NULL DEFAULT TRUE,

    CONSTRAINT pk_sec_classification PRIMARY KEY (code, parent_index_code),
    CONSTRAINT chk_sec_classification_type CHECK (type IN ('etf', 'index', 'stock')),
    CONSTRAINT chk_sec_classification_exchange CHECK (exchange IN ('SZ', 'SS', 'GEM', 'STAR', 'BJ', 'HK'))
);


COMMENT ON TABLE  stats.sec_classification                       IS 'Unified security metadata: ETF + Index + Stock classification + quality metrics. Labels are denormalized so callers need no JOIN to a catalog table. Replaces former etf_meta + index_meta + etf_index_map + stock_industry_map + sec_sector_industry_map.';
COMMENT ON COLUMN stats.sec_classification.code                  IS 'Security code. ETF/Stock: WITH exchange suffix (510050.SS, 000001.SZ). Index: bare 6-digit (000300).';
COMMENT ON COLUMN stats.sec_classification.type                  IS 'Security type: etf, index, or stock.';
COMMENT ON COLUMN stats.sec_classification.exchange              IS 'Listing board/market: SZ, SS, GEM (创业板), STAR (科创板), BJ, HK. NULL for cross-market indices.';
COMMENT ON COLUMN stats.sec_classification.sector_id             IS 'L1 classification id. When is_industry_not_strategy=TRUE: industry sector (FIN, TECH, HC, …). When FALSE: strategy id (BROAD, DIV, REGION, STRATEGY, SOE, OVERSEAS). Default OTHER.';
COMMENT ON COLUMN stats.sec_classification.sector_label          IS 'L1 label (denormalized). Chinese display string, e.g. 金融, 科技, 宽基, 红利.';
COMMENT ON COLUMN stats.sec_classification.industry_id           IS 'L2 classification id. When is_industry_not_strategy=TRUE: industry id (BANKS, SEMI, …). When FALSE: theme id (BROAD_CSI, DIV_SOE, …). Default OTHER.';
COMMENT ON COLUMN stats.sec_classification.industry_label        IS 'L2 label (denormalized). Chinese display string, e.g. 银行, 半导体, 中证, 央企/国企红利.';
COMMENT ON COLUMN stats.sec_classification.industry_slug         IS 'URL-safe slug = LOWER(industry_id), e.g. banks, semi, broad_csi, div_soe. Denormalized from catalog.';
COMMENT ON COLUMN stats.sec_classification.is_industry_not_strategy IS 'TRUE → sector_id/industry_id hold INDUSTRY classification (LEFT column). FALSE → they hold STRATEGY classification (RIGHT column). UI uses this flag to split display into two columns: left=industry, right=strategy.';
COMMENT ON COLUMN stats.sec_classification.n_days                IS 'Coverage: n_ohlcv_days for ETF, COUNT(*) for index/stock from identity table.';
COMMENT ON COLUMN stats.sec_classification.has_margin            IS 'ETF only: TRUE if any margin data exists. Populated by build_szse_sse_etf_and_margin.py.';
COMMENT ON COLUMN stats.sec_classification.avg_shares           IS 'ETF only: average daily volume in shares (converted × 10000 from source 万股). Populated by build_szse_sse_etf_and_margin.py.';
COMMENT ON COLUMN stats.sec_classification.selectivity_rank_score IS 'ETF only: composite score. Populated by build_szse_sse_etf_and_margin.py.';
COMMENT ON COLUMN stats.sec_classification.parent_index_code     IS 'Hierarchy: ETF → tracking index code; stock → ONE ROW PER qualifying index (weight >2%, non-BROAD); index → empty string. PK component, never NULL.';
COMMENT ON COLUMN stats.sec_classification.parent_index_weight   IS 'Stock only: weight_pct of the stock in its parent index. NULL for ETF/index.';
COMMENT ON COLUMN stats.sec_classification.parent_index_is_primary IS 'TRUE for the single authoritative parent of a security. Stock: row with MAX(parent_index_weight) per code. ETF: TRUE iff ETF name (issuer/manager prefix + legal suffix stripped) exactly matches parent index name. Index: always FALSE (root of hierarchy).';
COMMENT ON COLUMN stats.sec_classification.aum_yi                IS 'ETF only: net asset value (AUM) in 亿元 (100M yuan). Populated by build_classification.py from etf_index_map_all_*.csv 资产净值（亿元） column. NULL for index/stock.';
COMMENT ON COLUMN stats.sec_classification.owner_id             IS 'Logical reference to stats.sec_owners.owner_id. ETF: matched fund manager (issuer). Stock: company name (NULL when no curated owner exists). Index: NULL. Populated by build_classification.py from _common/sec_statics/sec_owners.json.';
COMMENT ON COLUMN stats.sec_classification.is_dummy             IS 'TRUE for synthetic industry dummy indices (type=''index'', code like DUMMY_BANKS). Created one per industry_id to serve as parent_index_code for orphan ETFs (ETFs with no CSV-mapped tracking index). FALSE for all real indices, ETFs, and stocks.';
COMMENT ON COLUMN stats.sec_classification.is_active            IS 'TRUE iff the security has >=1 record in the last year (trailing 365 days) in its identity table: index→stats.index_identity (bare 6-digit code), stock→stats.stock_identity (.SZ/.SS suffix), etf→stats.etf_identity (.SS/.SZ suffix). FALSE for delisted ETFs, dead indices, and old stocks with no recent data. Dummy indices (is_dummy=TRUE) are always TRUE — they are synthetic parents for orphan ETFs and have no identity records of their own. Populated by build_classification (sector_industry/upsert.py) as a post-upsert SQL UPDATE.';

-- Migrate: add is_dummy column to pre-existing installs.
ALTER TABLE stats.sec_classification ADD COLUMN IF NOT EXISTS is_dummy BOOLEAN NOT NULL DEFAULT FALSE;

-- Migrate: add is_active column to pre-existing installs.
ALTER TABLE stats.sec_classification ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

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
--  Populated by build_classification.py from _common/sec_statics/sec_classification.json `tags`
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
COMMENT ON COLUMN stats.sec_index_tags.industry_id  IS 'L2 industry id (e.g. DIV_SOE, MIL_DEFENSE, AERO_SPACE, BROAD_CSI300, BROAD_SSE50).';
COMMENT ON COLUMN stats.sec_index_tags.is_broad_market IS 'TRUE iff this index''s PRIMARY classification is the BROAD strategy (is_industry_not_strategy = FALSE AND the primary sector_id = ''BROAD''). Since sector_id now carries EITHER industry OR strategy based on is_industry_not_strategy, a strategy-primary index has sector_id=''BROAD'' directly. Industry-primary indices whose secondary tag happens to be BROAD (e.g. 中证银行 → FIN/BANKS primary + BROAD_CSI secondary tag) do NOT set this flag on any row. An index is considered broad-market if ANY of its tags has is_broad_market=TRUE. Populated by build_classification.py. BROAD themes are flagship index series (BROAD_SSE50/180/380, BROAD_CSI300/500/800/1000/2000, BROAD_CSI_A, BROAD_GEM, BROAD_STAR, BROAD_BSE, BROAD_TECH_INNOV, plus generic BROAD_SSE/BROAD_CSI/BROAD_SZSE catch-alls); each broad index carries exactly ONE BROAD theme (the most specific match wins, catch-alls dropped on collision).';

-- Add is_broad_market column to existing tables (no-op if already present).
-- Needed because CREATE TABLE IF NOT EXISTS does not add new columns to an
-- existing table — the column was added to the DDL after the table was first
-- created, so the live table lacks it until this ALTER runs.
ALTER TABLE stats.sec_index_tags ADD COLUMN IF NOT EXISTS is_broad_market BOOL;

-- Add is_industry_not_strategy column to sec_index_tags (denormalized from
-- sec_classification). Needed for is_broad_market derivation context.
ALTER TABLE stats.sec_index_tags ADD COLUMN IF NOT EXISTS is_industry_not_strategy BOOLEAN NOT NULL DEFAULT TRUE;

-- Drop legacy strategy columns from sec_index_tags. The tags table stores
-- ALL classifications per index in sector_id/industry_id (both industry and
-- strategy tags), so separate strategy columns were redundant denormalization.
ALTER TABLE stats.sec_index_tags DROP COLUMN IF EXISTS strategy_id;
ALTER TABLE stats.sec_index_tags DROP COLUMN IF EXISTS theme_id;

CREATE INDEX IF NOT EXISTS idx_sec_index_tags_sector_industry
    ON stats.sec_index_tags (sector_id, industry_id);


-- ----------------------------------------------------------------------------
--  Sec Owners — registry of security owners
--  For ETFs the owner is the fund manager / issuer (e.g. 南方, 华夏, 国泰).
--  For stocks the owner is the listed company itself.  Indices have no owner.
--
--  Source: _common/sec_statics/sec_owners.json (curated, hand-editable cache maintained alongside
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

COMMENT ON TABLE  stats.sec_owners               IS 'Registry of security owners (ETF fund managers / issuers, stock companies). Source: _common/sec_statics/sec_owners.json. Referenced logically by stats.sec_classification.owner_id.';
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



-- ----------------------------------------------------------------------------
-- Table: sec_similars
--   Per (composition_snapshot_date, code, sec_type) top-5 similar codes,
--   top-5 similar industry-classified peer codes (from DIFFERENT industries),
--   and top-5 dissimilar industry-classified peer codes by mutual shared
--   composition weight. Built by builds.index.exts._sec_similars from
--   stats.sec_composition + stats.sec_classification.
--
--   sec_type: 'index' and 'etf'. PK includes sec_type so both coexist.
--   Each sec_type is computed independently (index-vs-index, ETF-vs-ETF).
--
--   `date` is the COMPOSITION snapshot_date (quarterly/semi-annual), NOT a
--   trading day — downstream consumers look up the latest row with
--   date <= trading_date per code (same carry-forward pattern as
--   index_exts.stock_num). No FK to index_identity: composition dates do
--   not align with trading dates.
--
--   Sharing weight is MUTUAL and symmetric:
--     shared_weight_a = SUM(A.weight_pct) over stocks held by BOTH A and B
--     shared_weight_b = SUM(B.weight_pct) over stocks held by BOTH A and B
--     mutual_sharing_weight = (shared_weight_a + shared_weight_b) / 2
--   Ranking by this average is symmetric: A's view of B equals B's view of A.
--   Comparison uses each B's LATEST composition snapshot with snapshot_date
--   <= A's snapshot date (point-in-time). stock_code is matched on
--   LEFT(stock_code, 6) to ignore .SS/.SZ suffixes.
--
--   Industry-classified peer columns: the "industry_code" columns store SEC
--   CODES (individual index/ETF codes), NOT industry_ids. The "industry"
--   qualifier means the peer pool is filtered to securities where
--   sec_classification.is_industry_not_strategy=TRUE. The subject itself can
--   be either industry-primary or strategy-primary. Comparison is code-vs-code
--   (same mutual formula), just restricted to industry-classified peers.
--
--   Similar industry-classified peers — DISTINCT-INDUSTRY greedy selection:
--   The 1st pick is the peer with the highest mutual sharing weight. The 2nd
--   pick is the best peer from a DIFFERENT industry_id than the 1st. The 3rd
--   pick is the best peer from a different industry_id than both the 1st and
--   2nd, and so on through the 5th. Implemented as: rank peers within each
--   industry (best per industry wins), then rank those best-per-industry
--   peers by mutual DESC. The top-5 are the 5 most-similar peers from 5
--   different industries.
--
--   Dissimilar industry-classified peers: ranked by LOWEST mutual sharing
--   weight (ASC). Tie-breaker when sharing weights are equal (especially zero):
--   prefer peers in a DIFFERENT sector than the subject, then different
--   industry, maximizing classification distance.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.sec_similars (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    sec_type                  TEXT          NOT NULL CHECK (sec_type IN ('etf', 'index')),

    similar_1st_code_by_sharing_weights TEXT,
    similar_2nd_code_by_sharing_weights TEXT,
    similar_3rd_code_by_sharing_weights TEXT,
    similar_4th_code_by_sharing_weights TEXT,
    similar_5th_code_by_sharing_weights TEXT,
    similar_1st_code_sharing_weight_pct NUMERIC(8,4),
    similar_2nd_code_sharing_weight_pct NUMERIC(8,4),
    similar_3rd_code_sharing_weight_pct NUMERIC(8,4),
    similar_4th_code_sharing_weight_pct NUMERIC(8,4),
    similar_5th_code_sharing_weight_pct NUMERIC(8,4),

    similar_1st_industry_code_by_sharing_weights TEXT,
    similar_2nd_industry_code_by_sharing_weights TEXT,
    similar_3rd_industry_code_by_sharing_weights TEXT,
    similar_4th_industry_code_by_sharing_weights TEXT,
    similar_5th_industry_code_by_sharing_weights TEXT,
    similar_1st_industry_code_sharing_weight_pct NUMERIC(8,4),
    similar_2nd_industry_code_sharing_weight_pct NUMERIC(8,4),
    similar_3rd_industry_code_sharing_weight_pct NUMERIC(8,4),
    similar_4th_industry_code_sharing_weight_pct NUMERIC(8,4),
    similar_5th_industry_code_sharing_weight_pct NUMERIC(8,4),

    dissimilar_1st_industry_code_by_sharing_weights TEXT,
    dissimilar_2nd_industry_code_by_sharing_weights TEXT,
    dissimilar_3rd_industry_code_by_sharing_weights TEXT,
    dissimilar_4th_industry_code_by_sharing_weights TEXT,
    dissimilar_5th_industry_code_by_sharing_weights TEXT,
    dissimilar_1st_industry_code_sharing_weight_pct NUMERIC(8,4),
    dissimilar_2nd_industry_code_sharing_weight_pct NUMERIC(8,4),
    dissimilar_3rd_industry_code_sharing_weight_pct NUMERIC(8,4),
    dissimilar_4th_industry_code_sharing_weight_pct NUMERIC(8,4),
    dissimilar_5th_industry_code_sharing_weight_pct NUMERIC(8,4),

    CONSTRAINT pk_sec_similars PRIMARY KEY (date, code, sec_type)
);

COMMENT ON TABLE  stats.sec_similars                            IS 'Per (composition_snapshot_date, code, sec_type) top-5 similar codes + top-5 similar (distinct-industry) / dissimilar industry-classified peer codes by MUTUAL shared composition weight. Built by builds.index.exts._sec_similars from stats.sec_composition + stats.sec_classification. `date` is the composition snapshot_date (NOT a trading day). sec_type: index and etf (both populated). Similar industry peers are greedily selected from DIFFERENT industry_ids.';
COMMENT ON COLUMN stats.sec_similars.date                      IS 'COMPOSITION snapshot_date from stats.sec_composition. One row per (snapshot_date, code, sec_type) — NOT a trading day. Downstream consumers look up the latest row with date <= trading_date per code (same carry-forward pattern as index_exts.stock_num).';
COMMENT ON COLUMN stats.sec_similars.code                      IS 'Subject code (e.g. 000300, 931382 for index; 510050.SS for ETF). The security whose composition we are finding similars FOR.';
COMMENT ON COLUMN stats.sec_similars.sec_type                  IS 'Security type: index or etf. Discriminates index-vs-index vs ETF-vs-ETF similarity computations. Each sec_type is computed independently from sec_composition with matching source_type.';
COMMENT ON COLUMN stats.sec_similars.similar_1st_code_by_sharing_weights IS 'Code of the most-similar peer (highest mutual_sharing_weight). NULL when no other code shares any constituent stock.';
COMMENT ON COLUMN stats.sec_similars.similar_2nd_code_by_sharing_weights IS 'Code of the 2nd-most-similar peer by mutual_sharing_weight. NULL when fewer than 2 peers share constituents.';
COMMENT ON COLUMN stats.sec_similars.similar_3rd_code_by_sharing_weights IS 'Code of the 3rd-most-similar peer by mutual_sharing_weight. NULL when fewer than 3 peers share constituents.';
COMMENT ON COLUMN stats.sec_similars.similar_1st_code_sharing_weight_pct IS 'MUTUAL shared weight between the subject and the 1st-similar peer: (SUM(subject.weight_pct) + SUM(peer.weight_pct)) / 2 over constituent stocks held by BOTH. Symmetric. Range ~[0,100]; can slightly exceed 100 due to source-data rounding. Computed from each code''s LATEST composition snapshot <= this row''s date (point-in-time).';
COMMENT ON COLUMN stats.sec_similars.similar_2nd_code_sharing_weight_pct IS 'MUTUAL shared weight with the 2nd-similar peer. See similar_1st_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_3rd_code_sharing_weight_pct IS 'MUTUAL shared weight with the 3rd-similar peer. See similar_1st_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_1st_industry_code_by_sharing_weights IS 'SEC CODE of the most-similar INDUSTRY-CLASSIFIED peer (highest mutual_sharing_weight). "industry" means the peer pool is filtered to securities where sec_classification.is_industry_not_strategy=TRUE. The subject itself can be industry- or strategy-primary. Stores a code (e.g. 000300 for index, 510050.SS for ETF), NOT an industry_id. NULL when no industry-classified peer shares constituents. The 5 similar industry peers are from 5 DIFFERENT industry_ids (distinct-industry greedy selection).';
COMMENT ON COLUMN stats.sec_similars.similar_2nd_industry_code_by_sharing_weights IS 'SEC CODE of the 2nd-most-similar industry-classified peer, from a DIFFERENT industry_id than the 1st. See similar_1st_industry_code_by_sharing_weights.';
COMMENT ON COLUMN stats.sec_similars.similar_3rd_industry_code_by_sharing_weights IS 'SEC CODE of the 3rd-most-similar industry-classified peer, from a DIFFERENT industry_id than the 1st and 2nd. See similar_1st_industry_code_by_sharing_weights.';
COMMENT ON COLUMN stats.sec_similars.similar_1st_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 1st-similar industry-classified peer. Same formula as similar_1st_code_sharing_weight_pct, just restricted to industry-classified peers.';
COMMENT ON COLUMN stats.sec_similars.similar_2nd_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 2nd-similar industry-classified peer. See similar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_3rd_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 3rd-similar industry-classified peer. See similar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_1st_industry_code_by_sharing_weights IS 'SEC CODE of the most-DISSIMILAR industry-classified peer (LOWEST mutual_sharing_weight). Tie-breaker: prefer different sector than the subject, then different industry, maximizing classification distance. NULL when fewer than 1 industry-classified peer exists.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_2nd_industry_code_by_sharing_weights IS 'SEC CODE of the 2nd-most-dissimilar industry-classified peer. See dissimilar_1st_industry_code_by_sharing_weights.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_3rd_industry_code_by_sharing_weights IS 'SEC CODE of the 3rd-most-dissimilar industry-classified peer. See dissimilar_1st_industry_code_by_sharing_weights.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_1st_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 1st-DISSIMILAR industry-classified peer (lowest). See similar_1st_industry_code_sharing_weight_pct for the weight definition; dissimilars rank by LOWEST mutual.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_2nd_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 2nd-dissimilar industry-classified peer. See dissimilar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_3rd_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 3rd-dissimilar industry-classified peer. See dissimilar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_4th_code_by_sharing_weights IS 'Code of the 4th-most-similar peer by mutual_sharing_weight. NULL when fewer than 4 peers share constituents.';
COMMENT ON COLUMN stats.sec_similars.similar_5th_code_by_sharing_weights IS 'Code of the 5th-most-similar peer by mutual_sharing_weight. NULL when fewer than 5 peers share constituents.';
COMMENT ON COLUMN stats.sec_similars.similar_4th_code_sharing_weight_pct IS 'MUTUAL shared weight with the 4th-similar peer. See similar_1st_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_5th_code_sharing_weight_pct IS 'MUTUAL shared weight with the 5th-similar peer. See similar_1st_code_sharing_weight_pct.';

COMMENT ON COLUMN stats.sec_similars.similar_4th_industry_code_by_sharing_weights IS 'SEC CODE of the 4th-most-similar INDUSTRY-CLASSIFIED peer (by mutual_sharing_weight). NULL when fewer than 4 industry-classified peers share constituents. Similar industry peers are selected from distinct industry_ids.';
COMMENT ON COLUMN stats.sec_similars.similar_5th_industry_code_by_sharing_weights IS 'SEC CODE of the 5th-most-similar INDUSTRY-CLASSIFIED peer (by mutual_sharing_weight). NULL when fewer than 5 industry-classified peers share constituents. Similar industry peers are selected from distinct industry_ids.';
COMMENT ON COLUMN stats.sec_similars.similar_4th_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 4th-similar industry-classified peer. Same formula as similar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_5th_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 5th-similar industry-classified peer. Same formula as similar_1st_industry_code_sharing_weight_pct.';

COMMENT ON COLUMN stats.sec_similars.dissimilar_4th_industry_code_by_sharing_weights IS 'SEC CODE of the 4th-most-dissimilar industry-classified peer (LOWEST mutual_sharing_weight). NULL when fewer than 4 industry-classified peers exist.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_5th_industry_code_by_sharing_weights IS 'SEC CODE of the 5th-most-dissimilar industry-classified peer (LOWEST mutual_sharing_weight). NULL when fewer than 5 industry-classified peers exist.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_4th_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 4th-DISSIMILAR industry-classified peer (lowest). See similar_1st_industry_code_sharing_weight_pct for the weight definition.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_5th_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 5th-DISSIMILAR industry-classified peer (lowest). See similar_1st_industry_code_sharing_weight_pct for the weight definition.';
