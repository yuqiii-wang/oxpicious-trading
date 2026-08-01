-- ============================================================================
--  Index Extensions - supplementary per-(date, index) metrics
--  Table: index_exts
--    Extends the index baseline tables with derived metrics that are not part
--    of the raw CSIndex/SZSE daily OHLCV feed. Currently stores:
--      etf_num         = number of ETFs tracking this index on this date
--                        (identified via stats.sec_classification.
--                        parent_index_code = code). NULL/0 when no ETF tracks
--                        the index (e.g. 000001 上证指数).
--      total_etf_amt   = Σ etf_liquidity_margin.amount_wan × 1e4 (yuan) across
--                        ALL ETFs tracking this index on this date. NULL when
--                        no ETF tracks the index. Used by the perf-attribution
--                        analysis as the index's ETF-market trading volume.
--      total_etf_amt_ma5 = 5-trading-day moving average of total_etf_amt
--                        (AVG over the trailing 5 rows per code, ordered by
--                        date). NULL for the first 4 days of a code's history.
--
--    PK: (date, code) — same as index_identity, with a FK referencing it.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: index_exts
--   Per-(date, index_code) extension metrics (FK to index_identity)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.index_exts (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    etf_num                   INTEGER,
    total_etf_amt             NUMERIC(18,4),
    total_etf_amt_ma5         NUMERIC(18,4),

    CONSTRAINT pk_index_exts PRIMARY KEY (date, code),
    CONSTRAINT fk_index_exts_date_code FOREIGN KEY (date, code) REFERENCES stats.index_identity(date, code)
);

-- Add columns to existing tables (no-op if already present). CREATE TABLE IF
-- NOT EXISTS cannot add columns to an already-existing table, so the ALTERs
-- below migrate pre-existing installs.
ALTER TABLE stats.index_exts ADD COLUMN IF NOT EXISTS total_etf_amt     NUMERIC(18,4);
ALTER TABLE stats.index_exts ADD COLUMN IF NOT EXISTS total_etf_amt_ma5 NUMERIC(18,4);

COMMENT ON TABLE  stats.index_exts                  IS 'Index extension metrics: one row per (date, index_code). Stores etf_num (count of ETFs tracking this index), total_etf_amt (Σ ETF turnover tracking this index, yuan), and total_etf_amt_ma5 (5-day MA of total_etf_amt). Sourced via stats.sec_classification.parent_index_code = code.';
COMMENT ON COLUMN stats.index_exts.etf_num          IS 'Number of ETFs tracking this index on this date. Source: COUNT(DISTINCT etf_liquidity_margin.code) where the ETF''s stats.sec_classification.parent_index_code = this index code. NULL when no ETF tracks the index (e.g. 000001 上证指数 has no direct ETF).';
COMMENT ON COLUMN stats.index_exts.total_etf_amt    IS 'Aggregate ETF trading turnover (yuan) on this date across ALL ETFs tracking this index. Source: Σ stats.etf_liquidity_margin.amount_wan × 1e4 where the ETF''s stats.sec_classification.parent_index_code = this index code. NULL when no ETF tracks the index. Consumed by analyze_sec_alloc_perf_attribution.py as the benchmark/index ETF-market trading volume.';
COMMENT ON COLUMN stats.index_exts.total_etf_amt_ma5 IS '5-trading-day moving average of total_etf_amt (AVG over the trailing 5 rows per code ordered by date). NULL for the first 4 rows of a code''s history.';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_index_exts_code_date
    ON stats.index_exts (code, date);

-- ----------------------------------------------------------------------------
-- Table: etf_trading_amt
--   Per-(date, industry_id) aggregate ETF trading turnover.
--   `code` is an industry_id (e.g. BANKS, SEMI, BROAD_CSI) — aggregates ALL
--   ETFs whose linked parent index (stats.sec_classification.parent_index_code)
--   carries that industry classification. Built by build_index_exts.py from
--   the same etf_liquidity_margin source as index_exts, but grouped by the
--   linked index's industry_id instead of by index code.
--   Consumed by the Perf-Attr "Industry Trading Amt contribution" chart.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_trading_amt (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    etf_num                   INTEGER,
    total_etf_amt             NUMERIC(18,4),
    total_etf_amt_ma5         NUMERIC(18,4),

    CONSTRAINT pk_etf_trading_amt PRIMARY KEY (date, code)
);

-- Add columns to existing tables (no-op if already present). CREATE TABLE IF
-- NOT EXISTS cannot add columns to an already-existing table, so the ALTERs
-- below migrate pre-existing installs.
ALTER TABLE stats.etf_trading_amt ADD COLUMN IF NOT EXISTS etf_num           INTEGER;
ALTER TABLE stats.etf_trading_amt ADD COLUMN IF NOT EXISTS total_etf_amt     NUMERIC(18,4);
ALTER TABLE stats.etf_trading_amt ADD COLUMN IF NOT EXISTS total_etf_amt_ma5 NUMERIC(18,4);

COMMENT ON TABLE  stats.etf_trading_amt                  IS 'Per-(date, industry_id) aggregate ETF trading turnover. `code` is an industry_id (e.g. BANKS, SEMI, BROAD_CSI) — aggregates ALL ETFs whose linked parent index (stats.sec_classification.parent_index_code) carries that industry classification. Sourced via etf_liquidity_margin JOIN sec_classification(etf→parent_index_code) JOIN sec_classification(index→industry_id).';
COMMENT ON COLUMN stats.etf_trading_amt.code             IS 'Industry id (e.g. BANKS, SEMI, BROAD_CSI). Maps to stats.sec_classification.industry_id of the ETF''s linked parent index (parent_index_code → index''s industry_id). NOT an index code — industry ids are alpha strings, distinct from 6-digit index codes.';
COMMENT ON COLUMN stats.etf_trading_amt.etf_num          IS 'Number of ETFs whose linked parent index carries this industry_id on this date. Source: COUNT(DISTINCT etf_liquidity_margin.code).';
COMMENT ON COLUMN stats.etf_trading_amt.total_etf_amt    IS 'Aggregate ETF trading turnover (yuan) on this date across ALL ETFs whose linked parent index has this industry_id. Source: Σ etf_liquidity_margin.amount_wan × 1e4. Consumed by the Perf-Attr "Industry Trading Amt contribution" chart.';
COMMENT ON COLUMN stats.etf_trading_amt.total_etf_amt_ma5 IS '5-trading-day moving average of total_etf_amt (AVG over the trailing 5 rows per code ordered by date). NULL for the first 4 rows of a code''s history.';

-- Indexes
CREATE INDEX IF NOT EXISTS idx_etf_trading_amt_code_date
    ON stats.etf_trading_amt (code, date);

