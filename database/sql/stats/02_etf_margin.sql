-- ============================================================================
--  ETF Margin - Split Tables
--  Original: etf_margin table from schema.sql (fixed naming: etf_trend_and_margin → etf_margin)
--  Split into: etf_identity, etf_ohlcv, etf_adjustment, etf_liquidity_margin
--  Reconstruct via: v_etf_margin view (see 99_reconstruct_views.sql)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: etf_identity
--   Identity core (PK) for all etf_margin sub-tables
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_identity (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    exchange                  TEXT,
    name                      TEXT          NOT NULL,

    CONSTRAINT pk_etf_identity PRIMARY KEY (code, date),
    CONSTRAINT chk_etf_identity_code_format
        CHECK (code ~ '^\d{6}\.(SZ|SS|SH)$')
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_identity', 8);

-- Idempotent migration: replace the legacy code_suffix column with the
-- canonical exchange column (mirroring the canonical source-CSV schema).
ALTER TABLE stats.etf_identity ADD COLUMN IF NOT EXISTS exchange TEXT;
UPDATE stats.etf_identity
   SET exchange = split_part(code, '.', 2)
 WHERE exchange IS NULL OR exchange = '';
DROP INDEX IF EXISTS stats.idx_etf_identity_suffix_code_date;
ALTER TABLE stats.etf_identity DROP COLUMN IF EXISTS code_suffix;

COMMENT ON TABLE  stats.etf_identity                 IS 'ETF identity: one row per (date, etf_code). PK (code, date) shared by all ETF sub-tables. Native HASH partitioned by code.';
COMMENT ON COLUMN stats.etf_identity.code           IS 'ETF ticker with exchange suffix, e.g. "159007.SZ" (SZSE) or "510050.SS" (SSE).';
COMMENT ON COLUMN stats.etf_identity.exchange       IS 'Exchange of the code suffix: "SZ" or "SS". Carried from the canonical source-CSV exchange column; replaces the legacy code_suffix column.';

-- ----------------------------------------------------------------------------
-- Table: etf_ohlcv
--   ← Raw OHLCV (yuan)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_basic_stats (
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
    is_close_estimated        BOOLEAN       NOT NULL DEFAULT FALSE,
    has_intraday_5mins        BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_etf_basic_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_basic_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_basic_stats', 8);

-- Idempotent migration: add pe column to pre-existing tables.
ALTER TABLE stats.etf_basic_stats ADD COLUMN IF NOT EXISTS pe NUMERIC(18,4);
ALTER TABLE stats.etf_basic_stats ADD COLUMN IF NOT EXISTS eps NUMERIC(18,6);

COMMENT ON TABLE  stats.etf_basic_stats                    IS 'ETF raw basic_stats (yuan) + pe (harmonic-weighted constituent PE).';
COMMENT ON COLUMN stats.etf_basic_stats.is_close_estimated IS 'TRUE when close was estimated (not from source CSV). Estimation: for missing trading days, close is derived from prev_close adjusted by the percentage change of the most-similar index/ETF (highest composition shared weight > 60%). If no proxy qualifies, prev_close is carried forward.';
COMMENT ON COLUMN stats.etf_basic_stats.has_intraday_5mins IS 'TRUE when 5-minute intraday bars exist for this (date, code) (reserved for future ETF intraday support).';
COMMENT ON COLUMN stats.etf_basic_stats.pe                 IS 'Price-to-earnings ratio (PE). Computed by builds.etf via HARMONIC weighting of constituent stock PE from stats.stock_basic_stats by the LATEST stats.sec_composition snapshot (source_type=etf, temporal extrapolation): PE_etf = SUM(w_i) / SUM(w_i / PE_i). Loss-making constituents (NULL PE) excluded from both numerator and denominator. NULL when no composition or no constituent has positive PE.';
COMMENT ON COLUMN stats.etf_basic_stats.eps                IS 'Implied earnings per share (EPS), in yuan per single share, derived from the identity PE = price / EPS as eps = close / pe. NULL when pe is NULL (no composition or all constituents loss-making) or close is NULL. Populated by builds/etf/__main__.py at insert time (recomputed when the harmonic PE is backfilled).';

-- ----------------------------------------------------------------------------
-- Table: etf_tech_stats
--   ← Technical indicators (moving averages)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_tech_stats (
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

    CONSTRAINT pk_etf_tech_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_tech_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_tech_stats', 8);

-- Idempotent migration: add EMA columns to pre-existing tables.
-- CREATE TABLE IF NOT EXISTS does not add new columns to an existing
-- table, so the ALTER TABLE below is required for production upgrades
-- without a full rebuild. Runs BEFORE the COMMENT statements so the
-- columns exist when the comments are applied.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'stats' AND table_name = 'etf_tech_stats' AND column_name = 'ema6'
    ) THEN
        ALTER TABLE stats.etf_tech_stats
            ADD COLUMN ema6  NUMERIC(18,4),
            ADD COLUMN ema10 NUMERIC(18,4),
            ADD COLUMN ema20 NUMERIC(18,4),
            ADD COLUMN ema60 NUMERIC(18,4);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'stats' AND table_name = 'etf_tech_stats' AND column_name = 'ema120'
    ) THEN
        ALTER TABLE stats.etf_tech_stats
            ADD COLUMN ema120 NUMERIC(18,4),
            ADD COLUMN ema255 NUMERIC(18,4);
    END IF;
END $$;

COMMENT ON TABLE  stats.etf_tech_stats                    IS 'ETF technical indicators (moving averages + EMAs).';
COMMENT ON COLUMN stats.etf_tech_stats.ma5               IS '5-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma5_ratio         IS 'Close / MA5 - 1 (ratio of price to 5-day MA).';
COMMENT ON COLUMN stats.etf_tech_stats.ma20              IS '20-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma60              IS '60-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma120             IS '120-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma255             IS '255-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ema6              IS '6-day exponential moving average of adj_close (span=6, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema10             IS '10-day exponential moving average of adj_close (span=10, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema20             IS '20-day exponential moving average of adj_close (span=20, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema60             IS '60-day exponential moving average of adj_close (span=60, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema120            IS '120-day exponential moving average of adj_close (span=120, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema255            IS '255-day exponential moving average of adj_close (span=255, adjust=False).';

-- ----------------------------------------------------------------------------
-- Table: etf_adjustment
--   ← Split / dividend adjustment
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_adjustment (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    cum_split_factor          NUMERIC(18,8) NOT NULL DEFAULT 1.0,
    is_split_event_day        SMALLINT     NOT NULL DEFAULT 0
        CHECK (is_split_event_day IN (0,1)),
    action_type               TEXT,
    implied_dividend_per_share NUMERIC(18,6) NOT NULL DEFAULT 0,
    cum_dividend_per_share    NUMERIC(18,6) NOT NULL DEFAULT 0,
    adj_prev_close            NUMERIC(18,6),
    adj_open                  NUMERIC(18,6),
    adj_high                  NUMERIC(18,6),
    adj_low                   NUMERIC(18,6),
    adj_close                 NUMERIC(18,6),

    CONSTRAINT pk_etf_adjustment PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_adjustment_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_adjustment', 8);

COMMENT ON TABLE  stats.etf_adjustment               IS 'ETF split / dividend adjustment data.';
COMMENT ON COLUMN stats.etf_adjustment.cum_split_factor IS 'Cumulative split factor (1.0 = no split). Multiply raw OHLC by 1/cum_split_factor to back-adjust.';
COMMENT ON COLUMN stats.etf_adjustment.adj_close     IS 'Split-adjusted close; the frontend uses adj_* when present, otherwise falls back to raw.';

-- ----------------------------------------------------------------------------
-- Table: etf_liquidity_margin
--   ← Liquidity and margin balances
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_liquidity_margin (
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

    CONSTRAINT pk_etf_liquidity_margin PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_liquidity_margin_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_liquidity_margin', 8);

COMMENT ON TABLE  stats.etf_liquidity_margin         IS 'ETF liquidity (trading_shares/trading_amount) + margin balances.';
COMMENT ON COLUMN stats.etf_liquidity_margin.trading_shares IS 'ETF trading volume in shares. Source CSV stores 成交量(万股); converted to shares (× 10000) in builds/etf/__main__.py.';
COMMENT ON COLUMN stats.etf_liquidity_margin.trading_amount IS 'ETF trading turnover in yuan. Source CSV stores 成交金额(万元); converted to yuan (× 10000) in builds/etf/__main__.py. A 1000x error normalization fix is applied before conversion (see build script).';
COMMENT ON COLUMN stats.etf_liquidity_margin.rz_balance IS '融资余额 (yuan) — borrowed cash to buy the ETF; always non-negative.';
COMMENT ON COLUMN stats.etf_liquidity_margin.rq_balance_amt IS '融券余额 (yuan) — borrowed ETF value outstanding; SSE source computes as 融券余量 × (open+close)/2 mid price.';
COMMENT ON COLUMN stats.etf_liquidity_margin.total_balance IS 'rz_balance + rq_balance_amt — total margin outstanding.';

-- ----------------------------------------------------------------------------
-- Table: etf_intraday_5min
--   ← 5-minute intraday OHLCV bars streamed from the SSE fund tab
--   (https://www.sse.com.cn/market/price/report/ "刷新" button JSONP source,
--    /exchange/fund endpoint). Mirrors stock_intraday_5min: the SSE list
--    endpoint publishes today's CUMULATIVE volume, so per-bar volume is
--    derived by subtracting the previous bar's cumulative volume from the
--    current bar's.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_intraday_5min (
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

    CONSTRAINT pk_etf_intraday_5min PRIMARY KEY (code, date, time),
    CONSTRAINT fk_etf_intraday_5min_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Idempotent migration: replace the legacy code_suffix column with exchange.
ALTER TABLE stats.etf_intraday_5min ADD COLUMN IF NOT EXISTS exchange TEXT;
UPDATE stats.etf_intraday_5min
   SET exchange = split_part(code, '.', 2)
 WHERE exchange IS NULL OR exchange = '';
ALTER TABLE stats.etf_intraday_5min DROP COLUMN IF EXISTS code_suffix;
COMMENT ON COLUMN stats.etf_intraday_5min.exchange IS 'Exchange of the code suffix: "SZ" or "SS". Set by the streaming loaders; replaces the legacy code_suffix column.';

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('stats', 'etf_intraday_5min', 16);

COMMENT ON TABLE  stats.etf_intraday_5min              IS 'ETF 5-minute intraday OHLCV bars streamed from the SSE fund tab (https://www.sse.com.cn/market/price/report/). Mirrors stock_intraday_5min.';
COMMENT ON COLUMN stats.etf_intraday_5min.time         IS 'Bar end time (HH:MM:SS); timestamp of the last 1-minute sample in the bar, truncated to the minute.';
COMMENT ON COLUMN stats.etf_intraday_5min.open         IS 'Opening price of the 5-minute bar (first sample latest price).';
COMMENT ON COLUMN stats.etf_intraday_5min.high         IS 'Highest latest price during the 5-minute bar.';
COMMENT ON COLUMN stats.etf_intraday_5min.low          IS 'Lowest latest price during the 5-minute bar.';
COMMENT ON COLUMN stats.etf_intraday_5min.close        IS 'Closing price of the 5-minute bar (last sample latest price).';
COMMENT ON COLUMN stats.etf_intraday_5min.trading_shares       IS 'Volume traded during the 5-minute window in shares (cumulative day volume at bar end minus cumulative volume at previous bar end).';
COMMENT ON COLUMN stats.etf_intraday_5min.change       IS 'Absolute change from the bar''s open (close - open).';
COMMENT ON COLUMN stats.etf_intraday_5min.change_pct   IS 'Percentage change from the bar''s open (%) = (close - open) / open * 100.';

-- ----------------------------------------------------------------------------
-- Table: etf_composition_link  (REMOVED)
--   Was a write-only mirror of an in-memory merge_asof comp_match_date column.
--   No reader ever queried it; the composition pipeline now writes directly
--   to sec_composition. Dropped for cleanup; the v_etf_margin view no longer
--   LEFT JOINs it.
--   NOTE: v_etf_margin is dropped first because the old view referenced this
--   table; it is recreated in 99_reconstruct_views.sql without the JOIN.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS stats.v_etf_margin;
DROP TABLE IF EXISTS stats.etf_composition_link;

-- Indexes
DROP INDEX IF EXISTS stats.idx_etf_margin_code_date;

-- (a) Per-code lookups (latest first); INCLUDE (name, exchange) lets the
--     Index Only Scan return these columns without heap fetches. Mirrors
--     idx_stock_identity_code_date. (Plain (code, date) prefix is now served
--     by the PK (code, date) itself.)
CREATE INDEX IF NOT EXISTS idx_etf_identity_code_date
    ON stats.etf_identity (code, date DESC) INCLUDE (name, exchange);

-- Date-first scans across all codes (the old date-first PK served these;
-- PK is now (code, date) so an explicit date index restores the pattern).
CREATE INDEX IF NOT EXISTS idx_etf_identity_date
    ON stats.etf_identity (date);

-- (b) Exchange-filtered lookups — WHERE exchange='SZ'/'SS', then by code.
--     Supports check_identity(exchange=...) and listing all ETFs of one
--     exchange with latest-name-per-code via DISTINCT ON (code) ...
--     ORDER BY code, date DESC. Mirrors idx_stock_identity_exchange_code_date.
CREATE INDEX IF NOT EXISTS idx_etf_identity_exchange_code_date
    ON stats.etf_identity (exchange, code, date DESC);

-- Legacy (code, date) secondary indexes are now redundant with the
-- code-first PK — drop them and add date-first indexes instead.
DROP INDEX IF EXISTS stats.idx_etf_basic_stats_code_date;
DROP INDEX IF EXISTS stats.idx_etf_tech_stats_code_date;
DROP INDEX IF EXISTS stats.idx_etf_adjustment_code_date;
DROP INDEX IF EXISTS stats.idx_etf_liquidity_margin_code_date;

CREATE INDEX IF NOT EXISTS idx_etf_basic_stats_date
    ON stats.etf_basic_stats (date);
CREATE INDEX IF NOT EXISTS idx_etf_tech_stats_date
    ON stats.etf_tech_stats (date);
CREATE INDEX IF NOT EXISTS idx_etf_adjustment_date
    ON stats.etf_adjustment (date);
CREATE INDEX IF NOT EXISTS idx_etf_liquidity_margin_date
    ON stats.etf_liquidity_margin (date);

CREATE INDEX IF NOT EXISTS idx_etf_margin_split_events
    ON stats.etf_adjustment (date)
    WHERE is_split_event_day = 1;

-- (c) etf_intraday_5min — PK (code, date, time) now serves the per-code
--     lookups; the old (code, date, time)/(code, date)/(code) secondary
--     indexes are redundant and dropped. A date-first index restores
--     cross-code intraday scans.
DROP INDEX IF EXISTS stats.idx_etf_intraday_5min_code_date_time;
DROP INDEX IF EXISTS stats.idx_etf_intraday_5min_code_date;
DROP INDEX IF EXISTS stats.idx_etf_intraday_5min_code;

CREATE INDEX IF NOT EXISTS idx_etf_intraday_5min_date
    ON stats.etf_intraday_5min (date);