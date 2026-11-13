
-- ----------------------------------------------------------------------------
-- Table: index_intraday_5min
--   ← 5-minute intraday OHLCV bars (resampled from ~15s ticks)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each index's 5-minute intraday price bars within the trading day.
-- tags: index, 指数, intraday, 盘中, five-minute bars, 五分钟K线, ohlcv, K线

CREATE TABLE IF NOT EXISTS stats.index_intraday_5min (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    time                      TIME          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    change                    NUMERIC(18,4),
    change_pct                NUMERIC(10,4),

    CONSTRAINT pk_index_intraday_5min PRIMARY KEY (code, date, time)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'index_intraday_5min', 8);

COMMENT ON TABLE  stats.index_intraday_5min                    IS 'Index 5-minute intraday OHLCV bars.';
COMMENT ON COLUMN stats.index_intraday_5min.time               IS 'Bar end time (HH:MM:SS).';
COMMENT ON COLUMN stats.index_intraday_5min.open               IS 'Opening price of the 5-minute bar.';
COMMENT ON COLUMN stats.index_intraday_5min.high               IS 'Highest price during the 5-minute bar.';
COMMENT ON COLUMN stats.index_intraday_5min.low                IS 'Lowest price during the 5-minute bar.';
COMMENT ON COLUMN stats.index_intraday_5min.close              IS 'Closing price of the 5-minute bar.';
COMMENT ON COLUMN stats.index_intraday_5min.change             IS 'Absolute change from the bar''s open.';
COMMENT ON COLUMN stats.index_intraday_5min.change_pct         IS 'Percentage change from the bar''s open (%).';
CREATE INDEX IF NOT EXISTS idx_index_intraday_5min_date
    ON stats.index_intraday_5min (date);
