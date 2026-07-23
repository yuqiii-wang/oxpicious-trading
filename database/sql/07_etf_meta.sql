-- ============================================================================
--  ETF Meta — quality metrics for ETF ranking/ordering
--  Source: build_szse_sse_etf_and_margin.py (computed from v_etf_margin)
--  Purpose: order ETFs by data completeness (sufficient daily data > has margin
--           > highest trading volume) for the frontend theme browser.
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.etf_meta (
    code                      TEXT          NOT NULL,
    name                      TEXT          NOT NULL DEFAULT '',
    n_ohlcv_days              INTEGER       NOT NULL DEFAULT 0,
    has_margin                BOOLEAN       NOT NULL DEFAULT FALSE,
    avg_volume_wan            NUMERIC(24,4) NOT NULL DEFAULT 0,
    first_date                DATE,
    last_date                 DATE,
    data_quality_score        INTEGER       NOT NULL DEFAULT 0,
    sector_id                 TEXT          NOT NULL DEFAULT 'OTHER',
    sector_label              TEXT          NOT NULL DEFAULT '其他',
    industry_id               TEXT          NOT NULL DEFAULT 'OTHER',
    industry_label            TEXT          NOT NULL DEFAULT '未分类',
    industry_slug             TEXT          NOT NULL DEFAULT 'other',

    CONSTRAINT pk_etf_meta PRIMARY KEY (code)
);

-- Backward-compat: add columns if table was created by an older schema version.
ALTER TABLE stats.etf_meta ADD COLUMN IF NOT EXISTS sector_id     TEXT NOT NULL DEFAULT 'OTHER';
ALTER TABLE stats.etf_meta ADD COLUMN IF NOT EXISTS sector_label  TEXT NOT NULL DEFAULT '其他';
ALTER TABLE stats.etf_meta ADD COLUMN IF NOT EXISTS industry_id   TEXT NOT NULL DEFAULT 'OTHER';
ALTER TABLE stats.etf_meta ADD COLUMN IF NOT EXISTS industry_label TEXT NOT NULL DEFAULT '未分类';
ALTER TABLE stats.etf_meta ADD COLUMN IF NOT EXISTS industry_slug TEXT NOT NULL DEFAULT 'other';

COMMENT ON TABLE  stats.etf_meta                IS 'ETF quality metrics for ranking: n_ohlcv_days, has_margin, avg_volume_wan. Precomputed L1/L2 classification from _classification.py.';
COMMENT ON COLUMN stats.etf_meta.n_ohlcv_days  IS 'Number of daily OHLCV records (data completeness).';
COMMENT ON COLUMN stats.etf_meta.has_margin     IS 'TRUE if ETF has any margin data (rz_balance or rq_balance_qty > 0).';
COMMENT ON COLUMN stats.etf_meta.avg_volume_wan IS 'Average daily volume in 万 (10k) shares.';
COMMENT ON COLUMN stats.etf_meta.data_quality_score IS 'Composite score: (n_ohlcv_days>=200?100:0) + (has_margin?50:0) + min(avg_volume_wan_rank,50).';
COMMENT ON COLUMN stats.etf_meta.sector_id      IS 'L1 sector id from _classification.TAXONOMY (e.g. FIN, TECH, BROAD).';
COMMENT ON COLUMN stats.etf_meta.sector_label   IS 'L1 sector label (Chinese, e.g. 金融, 科技, 宽基).';
COMMENT ON COLUMN stats.etf_meta.industry_id    IS 'L2 industry/theme id (e.g. BANKS, SEMI, BROAD_CSI300).';
COMMENT ON COLUMN stats.etf_meta.industry_label IS 'L2 industry label (Chinese).';
COMMENT ON COLUMN stats.etf_meta.industry_slug  IS 'L2 industry slug (URL-safe, e.g. banks, semi, broad_csi300).';

CREATE INDEX IF NOT EXISTS idx_etf_meta_quality
    ON stats.etf_meta (data_quality_score DESC);

CREATE INDEX IF NOT EXISTS idx_etf_meta_sector_industry
    ON stats.etf_meta (sector_id, industry_id);
