
-- ----------------------------------------------------------------------------
-- Table: stock_basic_stats
--   ← Daily OHLC + pct_change (mirrors etf_basic_stats) + pe (stock-specific)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each stock's daily OHLC with percent change and price-earnings ratio.
-- tags: stock, 股票, ohlcv, K线, price change, 涨跌幅, pe, 市盈率

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
    is_ohl_estimated          BOOLEAN       NOT NULL DEFAULT FALSE,
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
COMMENT ON COLUMN stats.stock_basic_stats.is_ohl_estimated  IS 'TRUE when open/high/low were synthesized (not from source CSV) because the source row carries close-only data. Synthesis: open = previous close of the same code (NULL when no predecessor exists), high = low = close. FALSE when any real OHLC component came from the source.';
COMMENT ON COLUMN stats.stock_basic_stats.has_intraday_5mins IS 'TRUE when 5-minute intraday bars exist for this (date, code) (reserved for future stock intraday support).';

COMMENT ON COLUMN stats.stock_basic_stats.eps IS 'Earnings per share (EPS), in yuan per single share, derived from the identity PE = price / EPS as eps = close / pe. NULL when pe is NULL or <= 0 (loss-making / no PE recorded) or close is NULL. For SSE stocks where pe is estimated under the constant-EPS assumption (is_pe_estimated=TRUE), eps recovers that constant EPS = last_close / last_pe. Populated by builds/stock/__main__.py at insert time.';

CREATE INDEX IF NOT EXISTS idx_stock_basic_stats_date
    ON stats.stock_basic_stats (date);
