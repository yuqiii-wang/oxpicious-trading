-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each index's daily margin-financing curve — member stocks' balances weighted by index membership, or tracking-ETF margin for broad indices.
-- tags: margin, 融资融券, index, 指数, financing balance, 融资余额




-- ============================================================================
--  Margin Index Series (TABLE) — per-(index_code, date) weighted-average
--  RONGZI (融资) margin series aggregated from constituent stocks /
--  tracking ETFs.
--
--  MATERIALIZED TABLE (was a VIEW): the weighted-average aggregation moved
--  OUT of SQL into Python vectorization (analysis.margins pipeline step
--  build_margin_index_series). Per project rule, ALL INSERTs are in
--  Python — this file declares the schema only, no in-SQL computation.
--
--  Semantics (computed in pandas by analysis.margins.compute.
--  compute_index_margin_series):
--    index_margin_balance = Σ(rz_balance × w)  [rz_balance > 0]
--                          / Σ(w)              [rz_balance > 0]
--    index_margin_buy     = Σ(rz_buy × w)      [rz_buy > 0]
--                          / Σ(w)              [rz_buy > 0]
--  INVALID-VALUE EXCLUSION: constituents with rz_* = 0 / NULL are excluded
--  from BOTH numerator AND denominator.
--
--  Branch 1 (stock-based): indices with stock constituents in
--  sec_classification (weight must be > 0). Branch 2 (ETF-proxy): index
--  codes with NO stock constituents (broad-market / strategy indices) —
--  weighted-average of their TRACKING ETFs' margin (weight COALESCE 1.0).
--
--  Margin-trends related: source of the 'index' attribution series for the
--  Margin Trends page + the sec_type='index' histories for the
--  margin_changes trend detection. RONQIN (融券 / sec borrow) EXCLUDED.
-- ============================================================================
DROP VIEW  IF EXISTS analysis.margin_index_series;
DROP TABLE IF EXISTS analysis.margin_index_series;
CREATE TABLE analysis.margin_index_series (
    index_code           TEXT         NOT NULL,
    industry_id          TEXT,
    date                 DATE         NOT NULL,

    -- Weighted-average rongzi OUTSTANDING (yuan, STOCK).
    index_margin_balance NUMERIC(24,4),
    -- Weighted-average daily rongzi BUY amount (yuan, FLOW).
    index_margin_buy     NUMERIC(24,4),

    -- Constituents with a margin row on this date (includes rz_balance=0).
    n_constituents       INTEGER,
    -- Constituents with NON-ZERO rz_balance on this date.
    n_with_balance       INTEGER,

    CONSTRAINT pk_margin_index_series PRIMARY KEY (index_code, date)
) PARTITION BY HASH (index_code);

-- Native hash partitions (8) keyed by index_code
SELECT public.create_hash_partitions('analysis', 'margin_index_series', 8);

COMMENT ON TABLE  analysis.margin_index_series IS 'Per-(index_code, date) weighted-average RONGZI (融资) margin series MATERIALIZED as a table (was a VIEW — aggregation moved to Python vectorization). Branch 1 (stock-based): Σ(rz_* × parent_index_weight) / Σ(parent_index_weight) for indices with stock constituents. Branch 2 (ETF-proxy): for index codes with NO stock constituents (broad-market / strategy indices), weighted-average margin of their TRACKING ETFs (weight COALESCE 1.0). industry_id denormalized from the index''s own sec_classification row. Source of the ''index'' attribution series for the Margin Trends page + the sec_type=''index'' histories for margin_changes detection. RONQIN (融券) EXCLUDED. Built by analysis.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_index_series.index_code            IS 'Index code (bare 6-digit, e.g. 000970 for materials, 000300 for CSI300).';
COMMENT ON COLUMN analysis.margin_index_series.industry_id           IS 'Industry of the index, denormalized from the index''s own sec_classification row (type=''index''). NULL when the index code has no classification row.';
COMMENT ON COLUMN analysis.margin_index_series.date                  IS 'Trading date from stats.stock_liquidity_margin (stock branch) or stats.etf_liquidity_margin (ETF-proxy branch).';
COMMENT ON COLUMN analysis.margin_index_series.index_margin_balance  IS 'Weighted-average rongzi outstanding (yuan): Σ(rz_balance × parent_index_weight) / Σ(parent_index_weight) over constituents with rz_balance > 0.';
COMMENT ON COLUMN analysis.margin_index_series.index_margin_buy      IS 'Weighted-average daily rongzi BUY amount (yuan, FLOW): Σ(rz_buy × parent_index_weight) / Σ(parent_index_weight) over constituents with rz_buy > 0.';
COMMENT ON COLUMN analysis.margin_index_series.n_constituents        IS 'Number of constituents with a margin row on this date (includes rz_balance=0).';
COMMENT ON COLUMN analysis.margin_index_series.n_with_balance        IS 'Number of constituents with NON-ZERO rz_balance on this date.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_index_series', 'margin_index_series', NULL, NOW(),
     'Per-(index_code, date) weighted-average RONGZI (融资) margin series MATERIALIZED as a table (was a VIEW). Branch 1 (stock-based): weighted-average over stock constituents; Branch 2 (ETF-proxy): weighted-average over TRACKING ETFs for indices with NO stock constituents (weight COALESCE 1.0). Source of the ''index'' attribution series for the Margin Trends page + sec_type=''index'' histories for margin_changes detection. RONQIN EXCLUDED. Built by analysis.margins (Python vectorization, truncate-then-recompute).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;


