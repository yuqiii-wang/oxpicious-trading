-- ============================================================================
--  Margin Tech Stats — per-(sec_type, code, date) REGIME-DETECTION input for
--  the margin trend episode detection (analysis.margin_changes).
--
--  REDUCED SCHEMA: this table previously stored ma5/ma20/ma60 + slope + a
--  battery of slope-derivative columns for both margin_balance and
--  margin_buy. Only the two columns actually consumed by
--  analyze.margins.changes.detect_trend_episodes survive:
--    margin_balance_slope_ma5      — 5d smoothed balance slope (segmentation
--                                    signal: sign = UP/DOWN direction)
--    margin_balance_slope_zscore_20d — z-score of the daily slope vs its
--                                    20d window (significance filter)
--  Everything else (MAs, margin_buy slopes, slope_ma20/ma255/std20 raw
--  columns, CHECK constraints) was REMOVED in the margin cleanup.
--
--  Source tables:
--    stats.etf_liquidity_margin   — ETF margin balances (融资融券)
--    stats.stock_liquidity_margin — stock margin balances
--  'index' rows are the weighted-AVERAGE of constituent stocks' rz_balance
--  aggregated via the analysis.margin_index_series TABLE (aggregate-then-
--  compute; slope is a ratio / non-additive).
--
--  SCOPE — RONGZI ONLY (融资, cash borrow to buy). RONQIN (融券, sec
--  borrow) is INTENTIONALLY EXCLUDED per spec.
--
--  UNIVERSE FILTER (Python build script): only securities with at least
--  one non-zero rz_balance row in the last calendar month.
--
--  Table: analysis.margin_tech_stats
--    PK: (code, sec_type, date)
--
--  POPULATION: analyze.margins (Python, truncate-then-recompute). Per
--  project rule, ALL INSERTs are in Python — no raw INSERT...SELECT SQL.
--
--  Register in analysis.analysis_identity (name='margin_tech_stats').
-- ============================================================================
DROP TABLE IF EXISTS analysis.margin_tech_stats;

CREATE TABLE analysis.margin_tech_stats (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'stock' | 'index'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- 5d rolling mean of the daily balance slope (sign = trend direction).
    margin_balance_slope_ma5  NUMERIC(18,6),

    -- (slope - slope_ma20) / slope_std20. Significance filter input.
    margin_balance_slope_zscore_20d NUMERIC(18,6),

    CONSTRAINT pk_margin_tech_stats PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'margin_tech_stats', 16);

COMMENT ON TABLE  analysis.margin_tech_stats IS 'Per-(code, sec_type, date) regime-detection input for analysis.margin_changes trend episode detection. REDUCED to the two columns consumed by detect_trend_episodes: margin_balance_slope_ma5 (5d smoothed rz_balance slope; sign = UP/DOWN segmentation signal) and margin_balance_slope_zscore_20d (slope z-score vs 20d window; magnitude significance filter). sec_type ∈ {etf, stock, index} — ''index'' rows aggregated from the analysis.margin_index_series TABLE. RONGZI (融资) only. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_tech_stats.sec_type IS 'Subject security type: etf, stock, or index.';
COMMENT ON COLUMN analysis.margin_tech_stats.code IS 'Security ticker with exchange suffix, e.g. "159001.SZ" (ETF) or "600008.SS" (stock).';
COMMENT ON COLUMN analysis.margin_tech_stats.date IS 'Trading date.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope_ma5 IS '5-trading-day rolling mean of the daily rz_balance slope ((X[t]-X[t-1])/X[t-1]) per (sec_type, code). Sign = direction of the smoothed balance move — the segmentation signal for margin_changes trend episodes.';
COMMENT ON COLUMN analysis.margin_tech_stats.margin_balance_slope_zscore_20d IS '(slope - slope_ma20) / slope_std20 per (sec_type, code). NULL when the rolling std is NaN or <= 0. Magnitude-only significance filter: a margin_changes trend is kept when a MAJORITY of its days have |zscore| > 0.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_tech_stats', 'margin_tech_stats', NULL, NOW(),
     'Per-(sec_type, code, date) regime-detection input for analysis.margin_changes trend detection: margin_balance_slope_ma5 (segmentation signal) + margin_balance_slope_zscore_20d (significance filter). sec_type ∈ {etf, stock, index} (''index'' aggregated from analysis.margin_index_series TABLE). RONGZI only. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;


-- ============================================================================
--  Margin Industry Stats — per-(date, industry_id) SUM aggregation of stock
--  AND ETF RONGZI (融资) margin flows.
--
--  REDUCED SCHEMA: the diagnostic count / weight-share columns and the
--  GENERATED total_* columns were REMOVED in the margin cleanup. Only the
--  four SUM aggregates survive. The table's remaining consumer is the
--  margin-trends themes endpoint (industry universe with margin data).
--
--  Stock → industry via sec_classification (type='stock',
--  parent_index_is_primary=TRUE, parent_index_code <> '').
--  ETF   → industry via two-hop: parent_index_code → index.code →
--          index.industry_id (mirrors stats.etf_trading_amt).
--
--  Table: analysis.margin_industry_stats
--    PK: (industry_id, date)
--
--  POPULATION: analyze.margins (Python, truncate-then-recompute).
-- ============================================================================
DROP TABLE IF EXISTS analysis.margin_industry_stats;

CREATE TABLE analysis.margin_industry_stats (
    date                      DATE          NOT NULL,
    industry_id               TEXT          NOT NULL,

    -- Display label (denormalized, from stats.sec_classification)
    industry_label            TEXT          NOT NULL DEFAULT '',

    -- SUM(rz_balance) — rongzi OUTSTANDING (yuan, STOCK).
    stock_margin_balance      NUMERIC(24,4),
    etf_margin_balance        NUMERIC(24,4),

    -- SUM(rz_buy) — daily rongzi BUY amount (yuan, FLOW).
    stock_margin_buy          NUMERIC(24,4),
    etf_margin_buy            NUMERIC(24,4),

    CONSTRAINT pk_margin_industry_stats PRIMARY KEY (industry_id, date)
) PARTITION BY HASH (industry_id);

-- Native hash partitions (8) keyed by industry_id
SELECT public.create_hash_partitions('analysis', 'margin_industry_stats', 8);

COMMENT ON TABLE  analysis.margin_industry_stats IS 'Per-(industry_id, date) SUM aggregation of stock AND ETF RONGZI (融资) margin flows: stock/etf margin_balance (SUM rz_balance, yuan) and margin_buy (SUM rz_buy, yuan). Drives the margin-trends themes industry universe. RONGZI only. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_industry_stats.date             IS 'Trading date.';
COMMENT ON COLUMN analysis.margin_industry_stats.industry_id      IS 'Industry identifier (e.g. BANKS, SEMI). Maps from the primary parent index of each stock / the tracking index of each ETF via stats.sec_classification.';
COMMENT ON COLUMN analysis.margin_industry_stats.industry_label   IS 'Display label (denormalized from stats.sec_classification.industry_label).';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_balance IS 'SUM(stats.stock_liquidity_margin.rz_balance) across stocks whose primary industry = industry_id on this date. Yuan.';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_balance   IS 'SUM(stats.etf_liquidity_margin.rz_balance) across ETFs tracking indices in industry_id on this date. Yuan.';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_buy     IS 'SUM(stats.stock_liquidity_margin.rz_buy) across stocks whose primary industry = industry_id on this date. Yuan (FLOW — daily rongzi BUY amount / 融资买入额).';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_buy       IS 'SUM(stats.etf_liquidity_margin.rz_buy) across ETFs tracking indices in industry_id on this date. Yuan (FLOW).';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_industry_stats', 'margin_industry_stats', NULL, NOW(),
     'Per-(date, industry_id) SUM aggregation of stock AND ETF RONGZI (融资) margin flows: stock/etf margin_balance + margin_buy. Drives the margin-trends themes industry universe. RONGZI only. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;


-- ============================================================================
--  Margin Index Series (TABLE) — per-(index_code, date) weighted-average
--  RONGZI (融资) margin series aggregated from constituent stocks /
--  tracking ETFs.
--
--  MATERIALIZED TABLE (was a VIEW): the weighted-average aggregation moved
--  OUT of SQL into Python vectorization (analyze.margins pipeline step
--  build_margin_index_series). Per project rule, ALL INSERTs are in
--  Python — this file declares the schema only, no in-SQL computation.
--
--  Semantics (computed in pandas by analyze.margins.compute.
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

COMMENT ON TABLE  analysis.margin_index_series IS 'Per-(index_code, date) weighted-average RONGZI (融资) margin series MATERIALIZED as a table (was a VIEW — aggregation moved to Python vectorization). Branch 1 (stock-based): Σ(rz_* × parent_index_weight) / Σ(parent_index_weight) for indices with stock constituents. Branch 2 (ETF-proxy): for index codes with NO stock constituents (broad-market / strategy indices), weighted-average margin of their TRACKING ETFs (weight COALESCE 1.0). industry_id denormalized from the index''s own sec_classification row. Source of the ''index'' attribution series for the Margin Trends page + the sec_type=''index'' histories for margin_changes detection. RONQIN (融券) EXCLUDED. Built by analyze.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_index_series.index_code            IS 'Index code (bare 6-digit, e.g. 000970 for materials, 000300 for CSI300).';
COMMENT ON COLUMN analysis.margin_index_series.industry_id           IS 'Industry of the index, denormalized from the index''s own sec_classification row (type=''index''). NULL when the index code has no classification row.';
COMMENT ON COLUMN analysis.margin_index_series.date                  IS 'Trading date from stats.stock_liquidity_margin (stock branch) or stats.etf_liquidity_margin (ETF-proxy branch).';
COMMENT ON COLUMN analysis.margin_index_series.index_margin_balance  IS 'Weighted-average rongzi outstanding (yuan): Σ(rz_balance × parent_index_weight) / Σ(parent_index_weight) over constituents with rz_balance > 0.';
COMMENT ON COLUMN analysis.margin_index_series.index_margin_buy      IS 'Weighted-average daily rongzi BUY amount (yuan, FLOW): Σ(rz_buy × parent_index_weight) / Σ(parent_index_weight) over constituents with rz_buy > 0.';
COMMENT ON COLUMN analysis.margin_index_series.n_constituents        IS 'Number of constituents with a margin row on this date (includes rz_balance=0).';
COMMENT ON COLUMN analysis.margin_index_series.n_with_balance        IS 'Number of constituents with NON-ZERO rz_balance on this date.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_index_series', 'margin_index_series', NULL, NOW(),
     'Per-(index_code, date) weighted-average RONGZI (融资) margin series MATERIALIZED as a table (was a VIEW). Branch 1 (stock-based): weighted-average over stock constituents; Branch 2 (ETF-proxy): weighted-average over TRACKING ETFs for indices with NO stock constituents (weight COALESCE 1.0). Source of the ''index'' attribution series for the Margin Trends page + sec_type=''index'' histories for margin_changes detection. RONQIN EXCLUDED. Built by analyze.margins (Python vectorization, truncate-then-recompute).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;


-- ============================================================================
--  Dropped in the margin cleanup
-- ============================================================================
--  analysis.margin_industry_correlation — pairwise security correlation
--  table (never populated; population was deferred and the corr view was
--  removed from the Margin Trends page).
DELETE FROM analysis.analysis_identity WHERE name = 'margin_industry_correlation';
DROP TABLE IF EXISTS analysis.margin_industry_correlation;
