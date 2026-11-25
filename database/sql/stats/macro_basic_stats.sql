
-- ----------------------------------------------------------------------------
-- Table: macro_basic_stats
--   ← Daily OHLC + change + volume
--   Mirrors futures_basic_stats (no settlement/open-interest fields —
--   spot bars and offshore klines carry none)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each macro instrument's daily OHLC close series with day-over-day change and traded volume.
-- tags: macro, 宏观, ohlcv, K线, gold, 黄金, silver, 白银, close, 收盘价, volume, 成交量

CREATE TABLE IF NOT EXISTS stats.macro_basic_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    change                    NUMERIC(18,4),
    change_pct                NUMERIC(10,4),
    volume                    NUMERIC(24,4),

    CONSTRAINT pk_macro_basic_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_macro_basic_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.macro_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'macro_basic_stats', 8);

COMMENT ON TABLE  stats.macro_basic_stats               IS 'Macro daily OHLC + change + volume per instrument. Source CSVs under temps/gold_silver/ parsed by builds/macro/pipeline.py (SGE spot bars: OHLC only; COMEX klines: OHLC + volume).';
COMMENT ON COLUMN stats.macro_basic_stats.open          IS 'Opening price (开盘价).';
COMMENT ON COLUMN stats.macro_basic_stats.high          IS 'Highest price (最高价).';
COMMENT ON COLUMN stats.macro_basic_stats.low           IS 'Lowest price (最低价).';
COMMENT ON COLUMN stats.macro_basic_stats.close         IS 'Closing price (收盘价).';
COMMENT ON COLUMN stats.macro_basic_stats.change        IS 'Change vs the instrument''s previous trading-day close (涨跌 = 今收盘 - 前收盘), computed in builds/macro.';
COMMENT ON COLUMN stats.macro_basic_stats.change_pct    IS 'Percentage change vs the previous close in % (涨跌幅), computed in builds/macro.';
COMMENT ON COLUMN stats.macro_basic_stats.volume        IS 'Traded volume where the source publishes it (成交量; COMEX klines in lots, SGE spot bars publish none → NULL). Units differ per source — see macro_identity.source.';

-- ----------------------------------------------------------------------------
-- Indexes for macro_basic_stats
-- ----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_macro_basic_stats_date
    ON stats.macro_basic_stats (date);
