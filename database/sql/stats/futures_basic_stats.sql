
-- ----------------------------------------------------------------------------
-- Table: futures_basic_stats
--   ← Daily OHLCV + settlement + volume + open interest
--   Mirrors stock_basic_stats / index_basic_stats
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each futures contract's daily OHLC with settlement price, traded volume and open interest.
-- tags: futures, 期货, ohlcv, K线, settlement, 结算价, open interest, 持仓量

CREATE TABLE IF NOT EXISTS stats.futures_basic_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    settlement_price          NUMERIC(18,4),
    prev_settlement           NUMERIC(18,4),
    change                    NUMERIC(18,4),
    change_pct                NUMERIC(10,4),
    trading_shares            NUMERIC(24,4),
    trading_amount            NUMERIC(24,4),
    open_interest             NUMERIC(24,4),
    open_interest_change      NUMERIC(24,4),
    delta                     NUMERIC(18,8),

    CONSTRAINT pk_futures_basic_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_futures_basic_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.futures_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'futures_basic_stats', 8);

COMMENT ON TABLE  stats.futures_basic_stats               IS 'Futures daily OHLCV + settlement + volume + open interest. Source: CFFEX archive CSV columns mapped by builds/futures/loader.py.';
COMMENT ON COLUMN stats.futures_basic_stats.open          IS 'Opening price (今开盘).';
COMMENT ON COLUMN stats.futures_basic_stats.high          IS 'Highest price (最高价).';
COMMENT ON COLUMN stats.futures_basic_stats.low           IS 'Lowest price (最低价).';
COMMENT ON COLUMN stats.futures_basic_stats.close         IS 'Closing price (今收盘).';
COMMENT ON COLUMN stats.futures_basic_stats.settlement_price IS 'Daily settlement price (今结算).';
COMMENT ON COLUMN stats.futures_basic_stats.prev_settlement IS 'Previous settlement price (前结算). Used as the reference for change/change_pct.';
COMMENT ON COLUMN stats.futures_basic_stats.change        IS 'Change = close - previous settlement (涨跌1 = 今收盘 - 前结算).';
COMMENT ON COLUMN stats.futures_basic_stats.change_pct    IS 'Percentage change vs previous settlement (涨跌2 = % change).';
COMMENT ON COLUMN stats.futures_basic_stats.trading_shares IS 'Trading volume in contracts (成交量).';
COMMENT ON COLUMN stats.futures_basic_stats.trading_amount IS 'Trading turnover in yuan (成交金额).';
COMMENT ON COLUMN stats.futures_basic_stats.open_interest IS 'Open interest at end of day (持仓量, contracts).';
COMMENT ON COLUMN stats.futures_basic_stats.open_interest_change IS 'Change in open interest vs previous day (持仓变化).';
COMMENT ON COLUMN stats.futures_basic_stats.delta         IS 'Delta (always NULL for futures — CFFEX reports "--" which maps to NULL).';

-- ----------------------------------------------------------------------------
-- Indexes for futures_basic_stats
-- ----------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_futures_basic_stats_date
    ON stats.futures_basic_stats (date);

CREATE INDEX IF NOT EXISTS idx_futures_basic_stats_settlement
    ON stats.futures_basic_stats (settlement_price);
