-- ============================================================================
--  Index Meta — classification + coverage metrics for index ranking
--  Source: build_index_classification.py (computed from index_identity +
--           _classification.classify_index_full())
--  Purpose: classify indices by L1 sector + L2 industry for the frontend
--           two-level selector (mirrors stats.etf_meta).
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.index_meta (
    code                      TEXT          NOT NULL,
    name                      TEXT          NOT NULL DEFAULT '',
    n_days                    INTEGER       NOT NULL DEFAULT 0,
    first_date                DATE,
    last_date                 DATE,
    sector_id                 TEXT          NOT NULL DEFAULT 'OTHER',
    sector_label              TEXT          NOT NULL DEFAULT '其他',
    industry_id               TEXT          NOT NULL DEFAULT 'OTHER',
    industry_label            TEXT          NOT NULL DEFAULT '未分类',
    industry_slug             TEXT          NOT NULL DEFAULT 'other',

    CONSTRAINT pk_index_meta PRIMARY KEY (code)
);

COMMENT ON TABLE  stats.index_meta              IS 'Index classification + coverage metrics. Precomputed L1/L2 classification from _classification.py.';
COMMENT ON COLUMN stats.index_meta.code         IS 'Index code, e.g. "000300" (CSI300), "H30007" (chip industry).';
COMMENT ON COLUMN stats.index_meta.n_days       IS 'Number of daily records in index_identity for this code.';
COMMENT ON COLUMN stats.index_meta.first_date   IS 'Earliest date in index_identity for this code.';
COMMENT ON COLUMN stats.index_meta.last_date    IS 'Latest date in index_identity for this code.';
COMMENT ON COLUMN stats.index_meta.sector_id    IS 'L1 sector id from _classification.TAXONOMY (e.g. FIN, TECH, BROAD).';
COMMENT ON COLUMN stats.index_meta.sector_label IS 'L1 sector label (Chinese, e.g. 金融, 科技, 宽基).';
COMMENT ON COLUMN stats.index_meta.industry_id  IS 'L2 industry/theme id (e.g. BANKS, SEMI, BROAD_CSI300).';
COMMENT ON COLUMN stats.index_meta.industry_label IS 'L2 industry label (Chinese).';
COMMENT ON COLUMN stats.index_meta.industry_slug IS 'L2 industry slug (URL-safe, e.g. banks, semi, broad_csi300).';

CREATE INDEX IF NOT EXISTS idx_index_meta_sector_industry
    ON stats.index_meta (sector_id, industry_id);
