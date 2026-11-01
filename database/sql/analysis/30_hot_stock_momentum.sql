-- ============================================================================
--  Hot Stock Momentum — per-(sec_type, code, date) hot-stock momentum
--  curve for indices and composite industries.
--
--    Table: analysis.hot_stock_momentum
--    PK: (code, sec_type, date)
--
--  Serves the Commons "Hot Stock Momentum" page
--  (/analysis/commons/hot-stock-momentum) through /api/analysis/
--  hot-stock-momentum/* (themes / codes / chart). The UI nav is the
--  FULL classification universe (stats.sec_classification type='index'
--  + stats.industry_identity entities) — the nav is NOT restricted to
--  codes that already have rows, so the page is browsable while the
--  pipeline is still empty.
--
--  POPULATION
--    analysis.hot_stock_momentum (Python module). IMPLEMENTATION
--    PLACEHOLDER (2026-10-31): the module exists and registers its
--    service heartbeat, but run() is empty — no rows are written yet.
--    The schema is live so the API/UI wiring is complete; fill the
--    compute (fetch → hot-stock momentum curve → upsert) when the
--    metric definition lands. Per project rule, ALL INSERTs are in
--    Python — no raw INSERT...SELECT SQL in this file.
--
--    sec_type ∈ ('index' | 'industry') — the only sec_types the UI
--    exposes and the pipeline is scoped to (the shared label_dim
--    'sec_type' domain is 0=etf 1=index 2=stock 3=industry; the CHECK
--    keeps the full domain so the column decodes like every other
--    analysis sec_type column).
--
--  COLUMNS
--    momentum — the hot-stock momentum level of the security on the
--               date. Exact definition pending (the pipeline is the
--               placeholder); stored as NUMERIC(10,4) like the other
--               analysis ratio metrics. Rows exist only where the value
--               is defined — NULL / uncomputable days have NO row (the
--               analysis.pe invalid-value rule).
--
--  ============================================================================

CREATE TABLE IF NOT EXISTS analysis.hot_stock_momentum (
    sec_type        SMALLINT         NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code            TEXT             NOT NULL,
    date            DATE             NOT NULL,

    -- The hot-stock momentum curve value (definition pending — placeholder).
    momentum        NUMERIC(10,4)    NOT NULL,

    CONSTRAINT pk_hot_stock_momentum PRIMARY KEY (code, sec_type, date),
    CONSTRAINT chk_hot_stock_momentum_sec_type
        CHECK (sec_type IN (0, 1, 2, 3))  -- label codes: 0=etf 1=index 2=stock 3=industry
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'hot_stock_momentum', 16);

CREATE INDEX IF NOT EXISTS idx_hot_stock_momentum_date
    ON analysis.hot_stock_momentum (date);
CREATE INDEX IF NOT EXISTS idx_hot_stock_momentum_sec_type_date
    ON analysis.hot_stock_momentum (sec_type, date);

COMMENT ON TABLE  analysis.hot_stock_momentum IS 'Per-(code, sec_type, date) hot-stock momentum curve for indices (sec_type=1) and composite industries (sec_type=3, code = stats.sec_classification.industry_id). Serves the Commons Hot Stock Momentum page via /api/analysis/hot-stock-momentum/chart. IMPLEMENTATION PLACEHOLDER (2026-10-31): schema live, pipeline run() empty — no rows written yet. Built by analysis.hot_stock_momentum; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.hot_stock_momentum.sec_type IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index (CSI-style index), 2 = stock, 3 = industry (composite industry, code = stats.sec_classification.industry_id). This pipeline is scoped to 1 (index) and 3 (industry).';
COMMENT ON COLUMN analysis.hot_stock_momentum.code     IS 'Security code (bare index code e.g. 000001; sec_type=3: industry_id e.g. BANKS).';
COMMENT ON COLUMN analysis.hot_stock_momentum.date     IS 'Trading date.';
COMMENT ON COLUMN analysis.hot_stock_momentum.momentum IS 'The hot-stock momentum level (exact definition pending the pipeline implementation). Rows exist only where the value is defined — NULL / uncomputable days have NO row.';
