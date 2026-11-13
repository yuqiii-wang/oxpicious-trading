-- ============================================================================
--  Index Extensions - supplementary per-(date, index) metrics
--  Table: index_exts
--    Extends the index daily tables with derived metrics that are not part
--    of the raw CSIndex/SZSE daily OHLCV feed. Currently stores:
--      etf_num         = number of ETFs tracking this index on this date
--                        (identified via stats.sec_classification.
--                        parent_index_code = code). NULL/0 when no ETF tracks
--                        the index (e.g. 000001 上证指数).
--      total_etf_trading_amount = Σ etf_liquidity_margin.trading_amount (yuan) across
--                        ALL ETFs tracking this index on this date. NULL when
--                        no ETF tracks the index. Used by the perf-attribution
--                        analysis as the index's ETF-market trading volume.
--      total_etf_trading_amount_ma5 = 5-trading-day moving average of total_etf_trading_amount
--                        (AVG over the trailing 5 rows per code, ordered by
--                        date). NULL for the first 4 days of a code's history.
--
--    PK: (code, date) — same key set as index_identity (code-first), with a
--    FK referencing it. Native HASH partitioned by code.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: index_exts
--   Per-(date, index_code) extension metrics (FK to index_identity)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each index's ETF extension metrics — how many ETFs track it, their total turnover, and its constituent stock count.
-- tags: index, 指数, etf, turnover, 成交额, constituents, 成分股

CREATE TABLE IF NOT EXISTS stats.index_exts (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    etf_num                   INTEGER,
    total_etf_trading_amount  NUMERIC(18,4),
    total_etf_trading_amount_ma5 NUMERIC(18,4),
    stock_num                 INTEGER,

    CONSTRAINT pk_index_exts PRIMARY KEY (code, date),
    CONSTRAINT fk_index_exts_date_code FOREIGN KEY (code, date) REFERENCES stats.index_identity(code, date)

) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'index_exts', 8);

COMMENT ON TABLE  stats.index_exts                  IS 'Index extension metrics: one row per (date, index_code). Stores etf_num (count of ETFs tracking this index), total_etf_trading_amount (Σ ETF turnover tracking this index, yuan), total_etf_trading_amount_ma5 (5-day MA of total_etf_trading_amount), and stock_num (count of constituent stocks from the latest composition snapshot ≤ date). Sourced via stats.sec_classification.parent_index_code = code (ETF metrics) and stats.sec_composition (stock_num).';
COMMENT ON COLUMN stats.index_exts.etf_num          IS 'Number of ETFs tracking this index on this date. Source: COUNT(DISTINCT etf_liquidity_margin.code) where the ETF''s stats.sec_classification.parent_index_code = this index code. NULL when no ETF tracks the index (e.g. 000001 上证指数 has no direct ETF).';
COMMENT ON COLUMN stats.index_exts.total_etf_trading_amount    IS 'Aggregate ETF trading turnover (yuan) on this date across ALL ETFs tracking this index. Source: Σ stats.etf_liquidity_margin.trading_amount where the ETF''s stats.sec_classification.parent_index_code = this index code. NULL when no ETF tracks the index. Consumed by analyze_sec_alloc_perf_attribution.py as the index''s ETF-market trading volume.';
COMMENT ON COLUMN stats.index_exts.total_etf_trading_amount_ma5 IS '5-trading-day moving average of total_etf_trading_amount (AVG over the trailing 5 rows per code ordered by date). NULL for the first 4 days of a code''s history.';
COMMENT ON COLUMN stats.index_exts.stock_num        IS 'Number of constituent stocks in this index as of the latest stats.sec_composition snapshot_date <= this date (source_type=''index''). Source: COUNT(DISTINCT stock_code) GROUP BY code, snapshot_date, then LATERAL latest-snapshot lookup per (date, code). NULL when the index has no composition snapshot (e.g. cross-market H-prefixed indices without a CSI closeweight pull). Used by analyze_industry_sentiments.py to classify each index into a pool_size bucket: small (stock_num<51), mid (51-180), large (>180).';

-- Indexes
-- The legacy (code, date) index is superseded by the code-first PK; the
-- date-first index below keeps cross-code date scans cheap.
CREATE INDEX IF NOT EXISTS idx_index_exts_date
    ON stats.index_exts (date);
