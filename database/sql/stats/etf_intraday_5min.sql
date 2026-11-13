
-- ----------------------------------------------------------------------------
-- Table: etf_intraday_5min
--   ← 5-minute intraday OHLCV bars streamed from the SSE fund tab
--   (https://www.sse.com.cn/market/price/report/ "刷新" button JSONP source,
--    /exchange/fund endpoint). Mirrors stock_intraday_5min: the SSE list
--    endpoint publishes today's CUMULATIVE volume, so per-bar volume is
--    derived by subtracting the previous bar's cumulative volume from the
--    current bar's.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each ETF's 5-minute intraday price bars within the trading day.
-- tags: etf, intraday, 盘中, five-minute bars, 五分钟K线, ohlcv, K线

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

    CONSTRAINT pk_etf_intraday_5min PRIMARY KEY (code, date, time)
) PARTITION BY HASH (code);

COMMENT ON COLUMN stats.etf_intraday_5min.exchange IS 'Listing venue: "SZ" or "SS", set by the streaming loaders; NULL should not occur (streamer covers listed funds only).';

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

-- (c) etf_intraday_5min — PK (code, date, time) serves the per-code
--     lookups; a date-first index restores cross-code intraday scans.

CREATE INDEX IF NOT EXISTS idx_etf_intraday_5min_date
    ON stats.etf_intraday_5min (date);

