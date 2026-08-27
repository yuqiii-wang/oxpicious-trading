-- ============================================================================
--  Stock Baseline - Split Tables
--  Source: build_szse_sse_bse_stocks.py (from SZSE archive/trend + SSE trend + BSE trend CSVs)
--  Split into: stock_identity, stock_basic_stats, stock_liquidity_margin
--  Reconstruct via: v_stock_baseline view (see 99_reconstruct_views.sql)
--
--  Schema mirrors the ETF family (02_etf_margin.sql) for symmetry:
--    stock_identity          ~  etf_identity          (date, code, name)
--    stock_basic_stats       ~  etf_basic_stats       (date, code, prev_close, open,
--                                                      high, low, close, pct_change)
--    stock_liquidity_margin  ~  etf_liquidity_margin  (trading_shares, trading_amount,
--                                                      rz_*, rq_*, total_balance)
--  stock_basic_stats additionally keeps `pe` (stock-specific valuation).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: stock_identity
--   Identity core (PK) for all stock sub-tables
--   Mirrors etf_identity (date, code, name)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.stock_identity (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    exchange                  TEXT,
    board                     TEXT,
    name                      TEXT          NOT NULL DEFAULT '',
    is_in_index_or_etf        BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_stock_identity PRIMARY KEY (code, date),
    CONSTRAINT chk_stock_identity_code_format
        CHECK (code ~ '^\d{6}\.(SZ|SS|BJ)$')
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('stats', 'stock_identity', 16);

-- Idempotent migration: replace the legacy code_suffix column with the
-- canonical exchange + board columns (mirroring the canonical source-CSV
-- schema). Existing rows are backfilled from the code suffix / prefix.
ALTER TABLE stats.stock_identity ADD COLUMN IF NOT EXISTS exchange TEXT;
ALTER TABLE stats.stock_identity ADD COLUMN IF NOT EXISTS board    TEXT;
UPDATE stats.stock_identity
   SET exchange = split_part(code, '.', 2)
 WHERE exchange IS NULL OR exchange = '';
UPDATE stats.stock_identity
   SET board = CASE
        WHEN exchange = 'SS' AND code LIKE '688%' THEN 'STAR'
        WHEN exchange = 'SS' AND code LIKE '689%' THEN 'STAR'
        WHEN exchange = 'SZ' AND code LIKE '30%'  THEN 'GEM'
        WHEN exchange = 'BJ'                      THEN 'BSE'
        ELSE 'MAIN'
       END
 WHERE board IS NULL OR board = '';
DROP INDEX IF EXISTS stats.idx_stock_identity_suffix_code_date;
ALTER TABLE stats.stock_identity DROP COLUMN IF EXISTS code_suffix;

COMMENT ON TABLE  stats.stock_identity            IS 'Stock identity: one row per (date, code). PK (code, date) shared by all stock sub-tables. Native HASH partitioned by code. Mirrors etf_identity.';
COMMENT ON COLUMN stats.stock_identity.code       IS 'Stock ticker with exchange suffix, e.g. "000001.SZ" (Ping An Bank) or "600000.SS" (Pudong Development Bank).';
COMMENT ON COLUMN stats.stock_identity.exchange   IS 'Exchange of the code suffix: "SZ", "SS", or "BJ". Carried from the canonical source-CSV exchange column; replaces the legacy code_suffix column.';
COMMENT ON COLUMN stats.stock_identity.board      IS 'Listing board: "MAIN", "STAR" (科创板 688/689.SS), "GEM" (创业板 30xxxx.SZ), or "BSE" (Beijing). Carried from the canonical source-CSV board column.';
COMMENT ON COLUMN stats.stock_identity.is_in_index_or_etf  IS 'TRUE when this stock appears in any ETF or index composition (stats.sec_composition source_type in etf/index). Populated by populate_is_in_etf.py; used by stream_szse_price.py to avoid runtime EXISTS subquery.';

-- ----------------------------------------------------------------------------
-- Table: stock_basic_stats
--   ← Daily OHLC + pct_change (mirrors etf_basic_stats) + pe (stock-specific)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.stock_basic_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    prev_close                NUMERIC(18,4),
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    pct_change                NUMERIC(10,4),
    pe                        NUMERIC(18,4),
    eps                       NUMERIC(18,6),
    is_pe_estimated           BOOLEAN       NOT NULL DEFAULT FALSE,
    is_close_estimated        BOOLEAN       NOT NULL DEFAULT FALSE,
    has_intraday_5mins        BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_stock_basic_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_stock_basic_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.stock_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('stats', 'stock_basic_stats', 16);


COMMENT ON TABLE  stats.stock_basic_stats             IS 'Stock daily OHLC + pct_change + pe. Source: SZSE archive/trend + SSE trend + SSE PE CSVs. trading_shares/trading_amount moved to stats.stock_liquidity_margin (mirrors etf_liquidity_margin split).';
COMMENT ON COLUMN stats.stock_basic_stats.prev_close  IS 'Previous closing price (yuan). 前收 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.open        IS 'Opening price (yuan). 开盘 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.high        IS 'High price (yuan). 最高 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.low         IS 'Low price (yuan). 最低 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.close       IS 'Closing price (yuan). 今收 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.pct_change  IS 'Daily pct change (%). 涨跌幅（%） from source CSV; SSE derives as change/prev_close*100.';
COMMENT ON COLUMN stats.stock_basic_stats.pe          IS 'Price-to-earnings ratio (PE). For SSE stocks (where the dayk endpoint does not publish PE), pe is merged from separate {code}_pe.csv files when available; otherwise it is estimated from the last actual PE assuming constant EPS (see is_pe_estimated). NULL when no actual PE has ever been recorded for the stock, or when source data contains "-", empty, or 0.0 (SZSE uses 市盈率=0 as a loss-making marker, treated as NULL).';
COMMENT ON COLUMN stats.stock_basic_stats.is_pe_estimated IS 'TRUE when pe was estimated from the last actual PE row using constant-EPS assumption: estimated_pe = today_close * last_pe / last_close. FALSE when pe comes directly from the source CSV (actual), or when pe is NULL because no prior actual PE exists to estimate from.';
COMMENT ON COLUMN stats.stock_basic_stats.is_close_estimated IS 'TRUE when close was estimated (not from source CSV). Estimation: for missing trading days, close is derived from prev_close adjusted by the percentage change of the most-similar index (highest composition shared weight > 60%). If no proxy index qualifies, prev_close is carried forward.';
COMMENT ON COLUMN stats.stock_basic_stats.has_intraday_5mins IS 'TRUE when 5-minute intraday bars exist for this (date, code) (reserved for future stock intraday support).';

-- Idempotent migration: add eps column (earnings per share = close / pe) to
-- pre-existing tables. ADD COLUMN IF NOT EXISTS is a no-op on fresh installs.
ALTER TABLE stats.stock_basic_stats ADD COLUMN IF NOT EXISTS eps NUMERIC(18,6);
COMMENT ON COLUMN stats.stock_basic_stats.eps IS 'Earnings per share (EPS), in yuan per single share, derived from the identity PE = price / EPS as eps = close / pe. NULL when pe is NULL or <= 0 (loss-making / no PE recorded) or close is NULL. For SSE stocks where pe is estimated under the constant-EPS assumption (is_pe_estimated=TRUE), eps recovers that constant EPS = last_close / last_pe. Populated by builds/stock/__main__.py at insert time.';

-- NOTE: trading_shares / trading_amount previously lived on stock_basic_stats.
-- They have been moved to stats.stock_liquidity_margin (below) to mirror the
-- ETF family pattern (etf_liquidity_margin). The migration is performed by
-- the DO block at the BOTTOM of this file: it (1) creates the new table,
-- (2) copies existing values from stock_basic_stats, then (3) drops the
-- legacy columns. The migration is idempotent (safe to re-run).

-- ----------------------------------------------------------------------------
-- Indexes for stock_identity
--   stock_identity is large (~3M+ rows, one per (date, code)) and serves as the
--   FK parent for all stock sub-tables. The dominant access patterns are:
--     (a) latest name/stats for one code:   WHERE code=$1 ORDER BY date DESC LIMIT 1
--     (b) all stocks of one exchange:         WHERE exchange='SZ' (or 'SS')
--     (c) bulk join by (date, code):         handled by the PK
-- ----------------------------------------------------------------------------

-- (a) Covering index for latest-per-code lookups — the dominant pattern used by
--     stream_szse_price.py / stream_sse_price.py load_target_stocks (LATERAL
--     ... ORDER BY date DESC LIMIT 1) and single-code name resolutions.
--     date DESC matches the "latest first" ordering so the planner can stop
--     after LIMIT 1 without a sort; INCLUDE (name, exchange) lets the
--     Index Only Scan return these columns without heap fetches.
CREATE INDEX IF NOT EXISTS idx_stock_identity_code_date
    ON stats.stock_identity (code, date DESC) INCLUDE (name, exchange);

-- Date-first scans across all codes (the old date-first PK served these;
-- PK is now (code, date) so an explicit date index restores the pattern).
CREATE INDEX IF NOT EXISTS idx_stock_identity_date
    ON stats.stock_identity (date);

-- (b) Exchange-filtered lookups — WHERE exchange='SZ'/'SS', then by code.
--     Supports listing all stocks of one exchange with latest-name-per-code
--     via DISTINCT ON (code) ... ORDER BY code, date DESC.
--     INCLUDE (name, is_in_index_or_etf) lets the Index Only Scan return these
--     columns without heap fetches (stream_szse_price.py filters on is_in_index_or_etf).
CREATE INDEX IF NOT EXISTS idx_stock_identity_exchange_code_date
    ON stats.stock_identity (exchange, code, date DESC) INCLUDE (name, is_in_index_or_etf);

-- Legacy (code, date) secondary indexes are now redundant with the
-- code-first PK — drop them and add date-first indexes instead.
DROP INDEX IF EXISTS stats.idx_stock_basic_stats_code_date;
CREATE INDEX IF NOT EXISTS idx_stock_basic_stats_date
    ON stats.stock_basic_stats (date);

-- ----------------------------------------------------------------------------
-- Table: stock_tech_stats
--   ← Technical indicators (moving averages) for individual stocks.
--   Mirrors stats.index_tech_stats / stats.etf_tech_stats. MAs are computed
--   from stats.stock_basic_stats.close by builds/stock/__main__.py (and a
--   one-time backfill via build_stock_tech_stats.py for existing rows).
--   Consumed by analyze.mov_ave_spread for the 'stock' sec_type branch.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.stock_tech_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    ma5                       NUMERIC(18,4),
    ma5_ratio                 NUMERIC(10,6),
    ma20                      NUMERIC(18,4),
    ma60                      NUMERIC(18,4),
    ma120                     NUMERIC(18,4),
    ma255                     NUMERIC(18,4),
    ema6                      NUMERIC(18,4),
    ema10                     NUMERIC(18,4),
    ema20                     NUMERIC(18,4),
    ema60                     NUMERIC(18,4),
    ema120                    NUMERIC(18,4),
    ema255                    NUMERIC(18,4),

    CONSTRAINT pk_stock_tech_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_stock_tech_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.stock_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('stats', 'stock_tech_stats', 16);

COMMENT ON TABLE  stats.stock_tech_stats                    IS 'Stock technical indicators (moving averages + EMAs), computed from stats.stock_basic_stats.close.';
COMMENT ON COLUMN stats.stock_tech_stats.ma5                IS '5-day moving average of close.';
COMMENT ON COLUMN stats.stock_tech_stats.ma5_ratio          IS 'Close / MA5 - 1 (ratio of price to 5-day MA).';
COMMENT ON COLUMN stats.stock_tech_stats.ma20               IS '20-day moving average of close.';
COMMENT ON COLUMN stats.stock_tech_stats.ma60               IS '60-day moving average of close.';
COMMENT ON COLUMN stats.stock_tech_stats.ma120              IS '120-day moving average of close.';
COMMENT ON COLUMN stats.stock_tech_stats.ma255              IS '255-day moving average of close.';
COMMENT ON COLUMN stats.stock_tech_stats.ema6               IS '6-day exponential moving average of close (span=6, adjust=False).';
COMMENT ON COLUMN stats.stock_tech_stats.ema10              IS '10-day exponential moving average of close (span=10, adjust=False).';
COMMENT ON COLUMN stats.stock_tech_stats.ema20              IS '20-day exponential moving average of close (span=20, adjust=False).';
COMMENT ON COLUMN stats.stock_tech_stats.ema60              IS '60-day exponential moving average of close (span=60, adjust=False).';
COMMENT ON COLUMN stats.stock_tech_stats.ema120             IS '120-day exponential moving average of close (span=120, adjust=False).';
COMMENT ON COLUMN stats.stock_tech_stats.ema255             IS '255-day exponential moving average of close (span=255, adjust=False).';

DROP INDEX IF EXISTS stats.idx_stock_tech_stats_code_date;
CREATE INDEX IF NOT EXISTS idx_stock_tech_stats_date
    ON stats.stock_tech_stats (date);

-- ----------------------------------------------------------------------------
-- Table: stock_intraday_5min
--   ← 5-minute intraday OHLCV bars streamed from the SSE price endpoint
--   (https://www.sse.com.cn/market/price/report/ "刷新" button JSONP source).
--   Mirrors index_intraday_5min but adds a `trading_shares` column: the SSE endpoint
--   publishes today's CUMULATIVE volume, so per-bar volume is derived by
--   subtracting the previous bar's cumulative volume from the current bar's.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.stock_intraday_5min (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    exchange                  TEXT,
    time                      TIME          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    trading_shares            NUMERIC(24,4),
    change                    NUMERIC(18,4),
    change_pct                NUMERIC(10,4),

    CONSTRAINT pk_stock_intraday_5min PRIMARY KEY (code, date, time),
    CONSTRAINT fk_stock_intraday_5min_date_code FOREIGN KEY (code, date) REFERENCES stats.stock_identity(code, date)
) PARTITION BY HASH (code);

-- Idempotent migration: replace the legacy code_suffix column with exchange.
ALTER TABLE stats.stock_intraday_5min ADD COLUMN IF NOT EXISTS exchange TEXT;
UPDATE stats.stock_intraday_5min
   SET exchange = split_part(code, '.', 2)
 WHERE exchange IS NULL OR exchange = '';
ALTER TABLE stats.stock_intraday_5min DROP COLUMN IF EXISTS code_suffix;
COMMENT ON COLUMN stats.stock_intraday_5min.exchange IS 'Exchange of the code suffix: "SZ", "SS", or "BJ". Set by the streaming loaders; replaces the legacy code_suffix column.';

-- Native hash partitions (32) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('stats', 'stock_intraday_5min', 32);

COMMENT ON TABLE  stats.stock_intraday_5min              IS 'Stock 5-minute intraday OHLCV bars streamed from the SSE price endpoint (https://www.sse.com.cn/market/price/report/).';
COMMENT ON COLUMN stats.stock_intraday_5min.time         IS 'Bar end time (HH:MM:SS); timestamp of the last 1-minute sample in the bar, truncated to the minute.';
COMMENT ON COLUMN stats.stock_intraday_5min.open         IS 'Opening price of the 5-minute bar (first sample latest price).';
COMMENT ON COLUMN stats.stock_intraday_5min.high         IS 'Highest latest price during the 5-minute bar.';
COMMENT ON COLUMN stats.stock_intraday_5min.low          IS 'Lowest latest price during the 5-minute bar.';
COMMENT ON COLUMN stats.stock_intraday_5min.close        IS 'Closing price of the 5-minute bar (last sample latest price).';
COMMENT ON COLUMN stats.stock_intraday_5min.trading_shares       IS 'Volume traded during the 5-minute window in shares (cumulative day volume at bar end minus cumulative volume at previous bar end).';
COMMENT ON COLUMN stats.stock_intraday_5min.change       IS 'Absolute change from the bar''s open (close - open).';
COMMENT ON COLUMN stats.stock_intraday_5min.change_pct   IS 'Percentage change from the bar''s open (%) = (close - open) / open * 100.';

-- PK (code, date, time) serves the per-code lookups; the old
-- (code, date, time)/(code, date)/(code) secondary indexes are redundant
-- and dropped. A date-first index restores cross-code intraday scans.
DROP INDEX IF EXISTS stats.idx_stock_intraday_5min_code_date_time;
DROP INDEX IF EXISTS stats.idx_stock_intraday_5min_code_date;
DROP INDEX IF EXISTS stats.idx_stock_intraday_5min_code;
CREATE INDEX IF NOT EXISTS idx_stock_intraday_5min_date
    ON stats.stock_intraday_5min (date);

-- ----------------------------------------------------------------------------
-- Table: stock_liquidity_margin
--   ← Liquidity (trading_shares/trading_amount) + margin balances (融资融券).
--   Mirrors stats.etf_liquidity_margin. trading_shares/trading_amount
--   previously lived on stock_basic_stats; they were moved here so the stock
--   family mirrors the ETF family split (basic_stats = OHLCV+PE only;
--   liquidity_margin = liquidity + margin).
--
--   Source: builds/stock/__main__.py reads SZSE + SSE margin detail CSVs
--   (temps/{szse,sse}_margin/{szse,sse}_margin_detail_YYYYMMDD.csv), filtering
--   to STOCK codes (excludes ETF prefixes 510xxx/511xxx/.../159xxx/150xxx).
--   SSE margin detail CSVs contain BOTH ETFs and stocks (the underlying API
--   returns all SSE-listed margin-eligible securities); the filter keeps only
--   the stock rows. SZSE margin detail CSVs likewise contain only stocks
--   (SZSE ETFs are not margin-eligible, so they don't appear there).
--
--   margin columns are 0 (not NULL) for stocks with no margin data on a given
--   date — most stocks have no margin activity most days, so the table is
--   sparse-but-non-NULL. trading_shares/trading_amount are populated from the
--   OHLCV source CSVs (szse_archive / szse_trend / sse_trend / bse_trend) and
--   are 0 only when the source row was PE-only (NULL OHLCV).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.stock_liquidity_margin (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    trading_shares            NUMERIC(24,4) NOT NULL DEFAULT 0,
    trading_amount            NUMERIC(24,4) NOT NULL DEFAULT 0,
    rz_buy                    NUMERIC(24,4) NOT NULL DEFAULT 0,
    rz_balance                NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_sell_qty               NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_balance_qty            NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_balance_amt            NUMERIC(24,4) NOT NULL DEFAULT 0,
    total_balance             NUMERIC(24,4) NOT NULL DEFAULT 0,

    CONSTRAINT pk_stock_liquidity_margin PRIMARY KEY (code, date),
    CONSTRAINT fk_stock_liquidity_margin_date_code FOREIGN KEY (code, date) REFERENCES stats.stock_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('stats', 'stock_liquidity_margin', 16);

COMMENT ON TABLE  stats.stock_liquidity_margin                   IS 'Stock liquidity (trading_shares/trading_amount) + margin balances. Mirrors stats.etf_liquidity_margin.';
COMMENT ON COLUMN stats.stock_liquidity_margin.trading_shares    IS 'Stock trading volume in SHARES. Source CSV stores 成交量(万股); converted to shares (× 10000) in builds/stock/__main__.py. NULL for PE-only rows (no OHLCV source) — those rows are NOT inserted into this table.';
COMMENT ON COLUMN stats.stock_liquidity_margin.trading_amount    IS 'Stock trading turnover in yuan. Source CSV stores 成交金额(万元); converted to yuan (× 10000). Used by analyze_industry_sentiments.py to compute total industry capital flow (SUM across union of member-index stocks).';
COMMENT ON COLUMN stats.stock_liquidity_margin.rz_balance        IS '融资余额 (yuan) — borrowed cash to buy the stock; always non-negative. Source: SZSE + SSE margin detail CSVs.';
COMMENT ON COLUMN stats.stock_liquidity_margin.rq_balance_amt    IS '融券余额 (yuan) — borrowed stock value outstanding. SSE detail CSV does NOT publish this column (only 融券余量 qty); it is 0 for SSE stocks. SZSE detail CSV publishes it directly.';
COMMENT ON COLUMN stats.stock_liquidity_margin.total_balance     IS 'rz_balance + rq_balance_amt — total margin outstanding. SSE detail CSV does NOT publish this column; it is rz_balance + 0 for SSE stocks. SZSE detail CSV publishes it directly.';

DROP INDEX IF EXISTS stats.idx_stock_liquidity_margin_code_date;
CREATE INDEX IF NOT EXISTS idx_stock_liquidity_margin_date
    ON stats.stock_liquidity_margin (date);

-- ----------------------------------------------------------------------------
-- Migration: move trading_shares/trading_amount from stock_basic_stats (legacy
-- location) to stock_liquidity_margin, then drop the legacy columns.
-- Idempotent: safe to re-run (the DO block checks for the column's existence
-- before copying/dropping). The migration preserves existing data so a
-- production DB can be upgraded without re-running the full build.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'stats'
          AND table_name   = 'stock_basic_stats'
          AND column_name  = 'trading_shares'
    ) THEN
        -- Copy existing liquidity data into stock_liquidity_margin. Margin
        -- columns default to 0 (the legacy stock_basic_stats had no margin
        -- data; margin is populated by re-running builds/stock after this
        -- migration, which reads the margin CSVs and upserts).
        INSERT INTO stats.stock_liquidity_margin
            (date, code, trading_shares, trading_amount,
             rz_buy, rz_balance, rq_sell_qty, rq_balance_qty,
             rq_balance_amt, total_balance)
        SELECT
            date, code,
            COALESCE(trading_shares, 0),
            COALESCE(trading_amount, 0),
            0, 0, 0, 0, 0, 0
        FROM stats.stock_basic_stats
        WHERE trading_shares IS NOT NULL OR trading_amount IS NOT NULL
        ON CONFLICT (date, code) DO UPDATE SET
            trading_shares = EXCLUDED.trading_shares,
            trading_amount = EXCLUDED.trading_amount;

        ALTER TABLE stats.stock_basic_stats DROP COLUMN trading_shares;
        ALTER TABLE stats.stock_basic_stats DROP COLUMN trading_amount;
    END IF;
END $$;