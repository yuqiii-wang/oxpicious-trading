-- ============================================================================
--  Security Composition — ALL constituent holdings per ETF or INDEX per
--  composition snapshot.
--
--  Source: build_szse_sse_etf_and_margin.py
--    · Full composition (comp_long): ALL holdings for ETFs, rank 1..N
--    · Index composition (CSI closeweight): ALL constituents for CSI indices
--
--  source_type distinguishes the data origin:
--    'etf'   — ETF holdings (code = ETF code with exchange suffix, e.g. 159001.SZ)
--    'index' — Index composition (code = bare index code, e.g. 930606)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: sec_composition
--   One row per (code, snapshot_date, rank).
--   - For ETFs (source_type='etf'): code = "159001.SZ" (6-digit + exchange suffix)
--   - For indices (source_type='index'): code = "930606" (bare 6-digit index code)
--   rank = 1 is the largest weight; rank can exceed 5 for full composition data.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.sec_composition (
    snapshot_date             DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    source_type               TEXT          NOT NULL DEFAULT 'etf',
    rank                      SMALLINT      NOT NULL,
    stock_code                TEXT,
    stock_name                TEXT,
    weight_pct                NUMERIC(10,6) NOT NULL DEFAULT 0,

    CONSTRAINT pk_sec_composition PRIMARY KEY (code, snapshot_date, rank),
    CONSTRAINT chk_sec_composition_code
        CHECK (code ~ '^(\d{6}|H\d{5})(\.(SZ|SS|SH))?$'),
    CONSTRAINT chk_sec_composition_source
        CHECK (source_type IN ('etf', 'index'))
);


COMMENT ON TABLE  stats.sec_composition                IS 'ALL constituent holdings per ETF or INDEX per composition snapshot.';
COMMENT ON COLUMN stats.sec_composition.snapshot_date   IS 'Composition snapshot date (one snapshot per ETF/index, applied forward via merge_asof).';
COMMENT ON COLUMN stats.sec_composition.code            IS 'ETF code (e.g. 159001.SZ) or index code (e.g. 930606, H30007 for CSI cross-market).';
COMMENT ON COLUMN stats.sec_composition.source_type     IS 'Data origin: "etf" = ETF holdings, "index" = index composition (CSI closeweight).';
COMMENT ON COLUMN stats.sec_composition.rank            IS 'Holding rank (1 = largest weight). Rank > 5 for full composition data.';

CREATE INDEX IF NOT EXISTS idx_sec_composition_snapshot_code
    ON stats.sec_composition (snapshot_date, code);

CREATE INDEX IF NOT EXISTS idx_sec_composition_stock
    ON stats.sec_composition (stock_code)
    WHERE stock_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sec_composition_source
    ON stats.sec_composition (source_type);
