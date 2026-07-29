-- ============================================================================
--  Security Mapping Tables
--  Contains: stock_industry_map and etf_index_map
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Stock Industry Map — maps each A-share stock to its East Money industry
--  Source: build_stock_industry.py
--  Used by the ETF composition pie chart to group holdings by industry.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stats.stock_industry_map (
    stock_code                TEXT          NOT NULL,
    stock_name                TEXT          NOT NULL DEFAULT '',
    industry                  TEXT          NOT NULL DEFAULT '',
    industry_code             TEXT,
    sector_id                 TEXT          NOT NULL DEFAULT 'OTHER',
    sector_label              TEXT          NOT NULL DEFAULT '其他',
    industry_id               TEXT          NOT NULL DEFAULT 'OTHER',

    CONSTRAINT pk_stock_industry_map PRIMARY KEY (stock_code)
);


COMMENT ON TABLE  stats.stock_industry_map              IS 'Stock → industry mapping (East Money classification). Used for ETF composition pie chart.';
COMMENT ON COLUMN stats.stock_industry_map.industry     IS 'East Money industry board name (e.g. 银行, 半导体, 医药制造).';
COMMENT ON COLUMN stats.stock_industry_map.industry_code IS 'East Money industry board code.';
COMMENT ON COLUMN stats.stock_industry_map.sector_id    IS 'L1 sector id from _classification.TAXONOMY (e.g. FIN, TECH, HC).';
COMMENT ON COLUMN stats.stock_industry_map.sector_label IS 'L1 sector label (Chinese, e.g. 金融, 科技, 医药).';
COMMENT ON COLUMN stats.stock_industry_map.industry_id  IS 'L2 industry id (e.g. BANKS, SEMI, INNO_DRUG).';

CREATE INDEX IF NOT EXISTS idx_stock_industry_map_industry
    ON stats.stock_industry_map (industry);

CREATE INDEX IF NOT EXISTS idx_stock_industry_map_sector
    ON stats.stock_industry_map (sector_id, industry_id);


-- ----------------------------------------------------------------------------
--  ETF Index Map — maps each ETF to its primary tracking index
--  Source: build_etf_index_map.py (derived from _classification.INDUSTRY_INDEX_MAP)
--  Used as a composition fallback when an ETF has no holdings data.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS stats.etf_index_map (
    etf_code                  TEXT          NOT NULL,
    etf_name                  TEXT          NOT NULL DEFAULT '',
    index_code                TEXT          NOT NULL DEFAULT '',
    index_name                TEXT          NOT NULL DEFAULT '',
    sector_id                 TEXT          NOT NULL DEFAULT 'OTHER',
    sector_label              TEXT          NOT NULL DEFAULT '其他',
    industry_id               TEXT          NOT NULL DEFAULT 'OTHER',
    industry_label            TEXT          NOT NULL DEFAULT '未分类',

    CONSTRAINT pk_etf_index_map PRIMARY KEY (etf_code)
);


COMMENT ON TABLE  stats.etf_index_map                  IS 'ETF → index mapping (primary tracking index). Used as composition fallback when ETF has no holdings.';
COMMENT ON COLUMN stats.etf_index_map.etf_code         IS 'ETF code (with exchange suffix, e.g. 510300.SS).';
COMMENT ON COLUMN stats.etf_index_map.etf_name         IS 'ETF name (Chinese).';
COMMENT ON COLUMN stats.etf_index_map.index_code       IS 'Primary tracking index code (e.g. 000300 for 沪深300).';
COMMENT ON COLUMN stats.etf_index_map.index_name       IS 'Primary tracking index name (Chinese, e.g. 沪深300).';
COMMENT ON COLUMN stats.etf_index_map.sector_id        IS 'L1 sector id from _classification.TAXONOMY (e.g. FIN, TECH, BROAD).';
COMMENT ON COLUMN stats.etf_index_map.sector_label     IS 'L1 sector label (Chinese, e.g. 金融, 科技, 宽基).';
COMMENT ON COLUMN stats.etf_index_map.industry_id      IS 'L2 industry/theme id (e.g. BANKS, SEMI, BROAD_CSI300).';
COMMENT ON COLUMN stats.etf_index_map.industry_label   IS 'L2 industry label (Chinese).';

CREATE INDEX IF NOT EXISTS idx_etf_index_map_index_code
    ON stats.etf_index_map (index_code)
    WHERE index_code <> '';

CREATE INDEX IF NOT EXISTS idx_etf_index_map_sector
    ON stats.etf_index_map (sector_id, industry_id);
