
-- ----------------------------------------------------------------------------
-- Table: index_basic_stats
--   ← Daily OHLCV + trading_shares + trading_amount + change metrics
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each index's daily open / high / low / close, traded shares and amount, and price change.
-- tags: index, 指数, ohlcv, K线, trading amount, 成交额, price change, 涨跌幅

CREATE TABLE IF NOT EXISTS stats.index_basic_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    trading_shares            NUMERIC(24,4),
    trading_amount            NUMERIC(24,4),
    change                     NUMERIC(18,4),
    change_pct                 NUMERIC(10,4),
    is_ohl_estimated           BOOLEAN       NOT NULL DEFAULT FALSE,
    has_intraday_5mins         BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_index_basic_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_index_basic_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.index_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'index_basic_stats', 8);

COMMENT ON TABLE  stats.index_basic_stats                    IS 'Index daily OHLCV + trading_shares + trading_amount + change metrics.';
COMMENT ON COLUMN stats.index_basic_stats.trading_shares             IS 'Index trading volume in shares (交易量).';
COMMENT ON COLUMN stats.index_basic_stats.trading_amount     IS 'Index trading turnover (成交金额, yuan). Source CSV column 成交金额(亿元) is multiplied by 1e8 to convert to yuan.';
COMMENT ON COLUMN stats.index_basic_stats.change             IS 'Absolute price change from previous close.';
COMMENT ON COLUMN stats.index_basic_stats.change_pct         IS 'Percentage change from previous close (%).';
COMMENT ON COLUMN stats.index_basic_stats.is_ohl_estimated  IS 'TRUE when open/high/low were synthesized (not from source CSV) because the source row carries close-only data (CSIndex publishes no intraday OHLC for thematic-index history before 2024-10-24). Synthesis: open = previous close of the same code (NULL when no predecessor exists), high = low = close. FALSE when any real OHLC component came from the source.';
COMMENT ON COLUMN stats.index_basic_stats.has_intraday_5mins IS 'TRUE when 5-minute intraday bars exist for this (date, code) in stats.index_intraday_5min.';


-- The legacy (code, date) secondary indexes are superseded by the
-- code-first PK; the date-first indexes below keep cross-code scans cheap.

CREATE INDEX IF NOT EXISTS idx_index_basic_stats_date
    ON stats.index_basic_stats (date);
