-- ============================================================================
--  Stock Industry Map — maps each A-share stock to its East Money industry
--  Source: build_stock_industry.py (via AkShare ak.stock_board_industry_*)
--  Used by the ETF composition pie chart to group holdings by industry.
-- ============================================================================

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
