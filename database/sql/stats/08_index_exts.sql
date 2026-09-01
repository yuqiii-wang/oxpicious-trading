-- ============================================================================
--  Index Extensions - supplementary per-(date, index) metrics
--  Table: index_exts
--    Extends the index baseline tables with derived metrics that are not part
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
-- Legacy (code, date) index is redundant with the code-first PK — replaced by
-- a date-first index to keep cross-code date scans cheap.
DROP INDEX IF EXISTS stats.idx_index_exts_code_date;
CREATE INDEX IF NOT EXISTS idx_index_exts_date
    ON stats.index_exts (date);

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
    total_etf_trading_amount  NUMERIC(18,4),
    total_etf_trading_amount_ma5 NUMERIC(18,4),

    CONSTRAINT pk_etf_trading_amt PRIMARY KEY (code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code (industry_id)
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_trading_amt', 8);

-- Add columns to existing tables (no-op if already present). CREATE TABLE IF
-- NOT EXISTS cannot add columns to an already-existing table, so the ALTERs
-- below migrate pre-existing installs.
ALTER TABLE stats.etf_trading_amt ADD COLUMN IF NOT EXISTS etf_num           INTEGER;
ALTER TABLE stats.etf_trading_amt ADD COLUMN IF NOT EXISTS total_etf_trading_amount     NUMERIC(18,4);
ALTER TABLE stats.etf_trading_amt ADD COLUMN IF NOT EXISTS total_etf_trading_amount_ma5 NUMERIC(18,4);

COMMENT ON TABLE  stats.etf_trading_amt                  IS 'Per-(date, industry_id) aggregate ETF trading turnover. `code` is an industry_id (e.g. BANKS, SEMI, BROAD_CSI) — aggregates ALL ETFs whose linked parent index (stats.sec_classification.parent_index_code) carries that industry classification. Sourced via etf_liquidity_margin JOIN sec_classification(etf→parent_index_code) JOIN sec_classification(index→industry_id).';
COMMENT ON COLUMN stats.etf_trading_amt.code             IS 'Industry id (e.g. BANKS, SEMI, BROAD_CSI). Maps to stats.sec_classification.industry_id of the ETF''s linked parent index (parent_index_code → index''s industry_id). NOT an index code — industry ids are alpha strings, distinct from 6-digit index codes.';
COMMENT ON COLUMN stats.etf_trading_amt.etf_num          IS 'Number of ETFs whose linked parent index carries this industry_id on this date. Source: COUNT(DISTINCT etf_liquidity_margin.code).';
COMMENT ON COLUMN stats.etf_trading_amt.total_etf_trading_amount    IS 'Aggregate ETF trading turnover (yuan) on this date across ALL ETFs whose linked parent index has this industry_id. Source: Σ etf_liquidity_margin.trading_amount. Consumed by the Perf-Attr "Industry Trading Amt contribution" chart.';
COMMENT ON COLUMN stats.etf_trading_amt.total_etf_trading_amount_ma5 IS '5-trading-day moving average of total_etf_trading_amount (AVG over the trailing 5 rows per code ordered by date). NULL for the first 4 rows of a code''s history.';

-- Indexes
-- Legacy (code, date) index is redundant with the code-first PK — replaced by
-- a date-first index.
DROP INDEX IF EXISTS stats.idx_etf_trading_amt_code_date;
CREATE INDEX IF NOT EXISTS idx_etf_trading_amt_date
    ON stats.etf_trading_amt (date);


-- ----------------------------------------------------------------------------
-- Table: exchange_trading_amt
--   Per-(date, exchange) aggregate trading turnover (yuan), sourced from a
--   single representative broad-market index per exchange rather than a true
--   exchange-wide sum. Each exchange is proxied by ONE benchmark index whose
--   stats.index_basic_stats.trading_amount is taken as that exchange's
--   total_trading_amount on each date.
--
--   Hardcoded representative indices (per build directive):
--     SZ (SZSE) -> 399001  (深证成指)
--     SS (SSE)  -> 000001  (上证指数)
--   Exchange labels follow the stats.sec_classification.exchange convention
--   (SZ/SS/BJ/HK/OVERSEAS), NOT the verbose SZSE/SSE names.
--
--   Built by builds.index._exchange_trading_amt from
--   stats.index_basic_stats of the representative indices. Consumed by the
--   Perf-Attr "Exchange Trading Amt" view.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.exchange_trading_amt (
    date                      DATE          NOT NULL,
    exchange                  TEXT          NOT NULL,
    index_code                TEXT,
    total_trading_amount      NUMERIC(24,4),

    CONSTRAINT pk_exchange_trading_amt PRIMARY KEY (exchange, date),
    CONSTRAINT fk_exchange_trading_amt_date_code FOREIGN KEY (index_code, date) REFERENCES stats.index_identity(code, date)
) PARTITION BY HASH (exchange);

-- Native hash partitions (8) keyed by exchange (the code-like dimension)
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'exchange_trading_amt', 8);

-- Migrate: add the (index_code, date) -> index_identity FK to pre-existing
-- installs (CREATE TABLE IF NOT EXISTS does not retro-fit constraints to
-- an already-existing table). Drops and re-creates the constraint so the
-- migration is idempotent. No-op on fresh installs where the CREATE TABLE
-- already declared the FK.
ALTER TABLE stats.exchange_trading_amt DROP CONSTRAINT IF EXISTS fk_exchange_trading_amt_date_code;
ALTER TABLE stats.exchange_trading_amt ADD CONSTRAINT fk_exchange_trading_amt_date_code
    FOREIGN KEY (index_code, date) REFERENCES stats.index_identity(code, date);

COMMENT ON TABLE  stats.exchange_trading_amt             IS 'Per-(date, exchange) aggregate trading turnover (yuan), proxied by ONE representative broad-market index per exchange. Hardcoded: SZ (SZSE) -> 399001 (深证成指), SS (SSE) -> 000001 (上证指数). total_trading_amount = stats.index_basic_stats.trading_amount of the representative index. Built by builds.index._exchange_trading_amt.';
COMMENT ON COLUMN stats.exchange_trading_amt.exchange             IS 'Exchange code (matches stats.sec_classification.exchange convention: SZ=SZSE, SS=SSE). Part of PK (exchange, date). Each exchange is represented by exactly one benchmark index (see index_code).';
COMMENT ON COLUMN stats.exchange_trading_amt.index_code          IS 'Representative index code whose trading_amount is used as this exchange''s total_trading_amount on this date. Hardcoded: SZ->399001 (深证成指), SS->000001 (上证指数). FK references stats.index_identity(code, date).';
COMMENT ON COLUMN stats.exchange_trading_amt.total_trading_amount IS 'Trading turnover (yuan) of the representative index on this date. Source: stats.index_basic_stats.trading_amount. Rows where the representative index has no trading_amount (estimated-close gaps) are NOT inserted.';

-- Indexes
-- Legacy (exchange, date) index is redundant with the exchange-first PK —
-- replaced by a date-first index.
DROP INDEX IF EXISTS stats.idx_exchange_trading_amt_exchange_date;
CREATE INDEX IF NOT EXISTS idx_exchange_trading_amt_date
    ON stats.exchange_trading_amt (date);
