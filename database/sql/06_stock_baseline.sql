-- ============================================================================
--  Stock Baseline - Split Tables
--  Source: build_szse_sse_bse_stocks.py (from SZSE archive/trend + SSE trend + BSE trend CSVs)
--  Split into: stock_identity, stock_basic_stats
--  Reconstruct via: v_stock_baseline view (see 99_reconstruct_views.sql)
--
--  Schema mirrors the ETF family (02_etf_margin.sql) for symmetry:
--    stock_identity  ~  etf_identity   (date, code, name)
--    stock_basic_stats ~ etf_basic_stats (date, code, prev_close, open,
--                                          high, low, close, pct_change)
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
    code_suffix               TEXT,
    name                      TEXT          NOT NULL DEFAULT '',

    CONSTRAINT pk_stock_identity PRIMARY KEY (date, code),
    CONSTRAINT chk_stock_identity_code_format
        CHECK (code ~ '^\d{6}\.(SZ|SS|BJ)$')
);


COMMENT ON TABLE  stats.stock_identity            IS 'Stock identity: one row per (date, code). PK shared by all stock sub-tables. Mirrors etf_identity.';
COMMENT ON COLUMN stats.stock_identity.code       IS 'Stock ticker with exchange suffix, e.g. "000001.SZ" (Ping An Bank) or "600000.SS" (Pudong Development Bank).';

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
    has_intraday_5mins        BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_stock_basic_stats PRIMARY KEY (date, code),
    CONSTRAINT fk_stock_basic_stats_date_code FOREIGN KEY (date, code) REFERENCES stats.stock_identity(date, code)
);


COMMENT ON TABLE  stats.stock_basic_stats             IS 'Stock daily OHLC + pct_change (mirrors etf_basic_stats) + pe. Source: SZSE archive/trend + SSE trend CSVs.';
COMMENT ON COLUMN stats.stock_basic_stats.prev_close  IS 'Previous closing price (yuan). 前收 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.open        IS 'Opening price (yuan). 开盘 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.high        IS 'High price (yuan). 最高 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.low         IS 'Low price (yuan). 最低 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.close       IS 'Closing price (yuan). 今收 from source CSV.';
COMMENT ON COLUMN stats.stock_basic_stats.pct_change  IS 'Daily pct change (%). 涨跌幅（%） from source CSV; SSE derives as change/prev_close*100.';
COMMENT ON COLUMN stats.stock_basic_stats.pe          IS 'Price-to-earnings ratio (PE); NULL for SSE stocks (SSE price endpoint does not publish PE).';
COMMENT ON COLUMN stats.stock_basic_stats.has_intraday_5mins IS 'TRUE when 5-minute intraday bars exist for this (date, code) (reserved for future stock intraday support).';

-- ----------------------------------------------------------------------------
-- Indexes for stock_identity
--   stock_identity is large (~3M+ rows, one per (date, code)) and serves as the
--   FK parent for all stock sub-tables. The dominant access patterns are:
--     (a) latest name/stats for one code:   WHERE code=$1 ORDER BY date DESC LIMIT 1
--     (b) all stocks of one exchange:         WHERE code_suffix='SZ' (or 'SS')
--     (c) bulk join by (date, code):         handled by the PK
-- ----------------------------------------------------------------------------

-- (a) Covering index for latest-per-code lookups — the dominant pattern used by
--     stream_szse_price.py / stream_sse_price.py load_target_stocks (LATERAL
--     ... ORDER BY date DESC LIMIT 1) and single-code name resolutions.
--     date DESC matches the "latest first" ordering so the planner can stop
--     after LIMIT 1 without a sort; INCLUDE (name, code_suffix) lets the
--     Index Only Scan return these columns without heap fetches.
CREATE INDEX IF NOT EXISTS idx_stock_identity_code_date
    ON stats.stock_identity (code, date DESC) INCLUDE (name, code_suffix);

-- (b) Exchange-filtered lookups — WHERE code_suffix='SZ'/'SS', then by code.
--     Supports listing all stocks of one exchange with latest-name-per-code
--     via DISTINCT ON (code) ... ORDER BY code, date DESC.
CREATE INDEX IF NOT EXISTS idx_stock_identity_suffix_code_date
    ON stats.stock_identity (code_suffix, code, date DESC);

CREATE INDEX IF NOT EXISTS idx_stock_basic_stats_code_date
    ON stats.stock_basic_stats (code, date);

-- ----------------------------------------------------------------------------
-- Table: stock_intraday_5min
--   ← 5-minute intraday OHLCV bars streamed from the SSE price endpoint
--   (https://www.sse.com.cn/market/price/report/ "刷新" button JSONP source).
--   Mirrors index_intraday_5min but adds a `volume` column: the SSE endpoint
--   publishes today's CUMULATIVE volume, so per-bar volume is derived by
--   subtracting the previous bar's cumulative volume from the current bar's.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.stock_intraday_5min (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    code_suffix               TEXT,
    time                      TIME          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    volume                    NUMERIC(24,4),
    change                    NUMERIC(18,4),
    change_pct                NUMERIC(10,4),

    CONSTRAINT pk_stock_intraday_5min PRIMARY KEY (date, code, time),
    CONSTRAINT fk_stock_intraday_5min_date_code FOREIGN KEY (date, code) REFERENCES stats.stock_identity(date, code)
);

COMMENT ON TABLE  stats.stock_intraday_5min              IS 'Stock 5-minute intraday OHLCV bars streamed from the SSE price endpoint (https://www.sse.com.cn/market/price/report/).';
COMMENT ON COLUMN stats.stock_intraday_5min.time         IS 'Bar end time (HH:MM:SS); timestamp of the last 1-minute sample in the bar, truncated to the minute.';
COMMENT ON COLUMN stats.stock_intraday_5min.open         IS 'Opening price of the 5-minute bar (first sample latest price).';
COMMENT ON COLUMN stats.stock_intraday_5min.high         IS 'Highest latest price during the 5-minute bar.';
COMMENT ON COLUMN stats.stock_intraday_5min.low          IS 'Lowest latest price during the 5-minute bar.';
COMMENT ON COLUMN stats.stock_intraday_5min.close        IS 'Closing price of the 5-minute bar (last sample latest price).';
COMMENT ON COLUMN stats.stock_intraday_5min.volume       IS 'Volume traded during the 5-minute window in shares (cumulative day volume at bar end minus cumulative volume at previous bar end).';
COMMENT ON COLUMN stats.stock_intraday_5min.change       IS 'Absolute change from the bar''s open (close - open).';
COMMENT ON COLUMN stats.stock_intraday_5min.change_pct   IS 'Percentage change from the bar''s open (%) = (close - open) / open * 100.';

CREATE INDEX IF NOT EXISTS idx_stock_intraday_5min_code_date_time
    ON stats.stock_intraday_5min (code, date, time);
CREATE INDEX IF NOT EXISTS idx_stock_intraday_5min_code_date
    ON stats.stock_intraday_5min (code, date);
CREATE INDEX IF NOT EXISTS idx_stock_intraday_5min_code
    ON stats.stock_intraday_5min (code);