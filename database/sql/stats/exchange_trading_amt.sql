

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
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each exchange's total traded amount per day — the market-wide participation base behind market-share gauges.
-- tags: trading amount, 成交额, market share, 成交占比, exchange, 交易所, whole market, 全市场, two exchanges, 两市, 沪深

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

COMMENT ON TABLE  stats.exchange_trading_amt             IS 'Per-(date, exchange) aggregate trading turnover (yuan), proxied by ONE representative broad-market index per exchange. Hardcoded: SZ (SZSE) -> 399001 (深证成指), SS (SSE) -> 000001 (上证指数). total_trading_amount = stats.index_basic_stats.trading_amount of the representative index. Built by builds.index._exchange_trading_amt.';
COMMENT ON COLUMN stats.exchange_trading_amt.exchange             IS 'Exchange code (matches stats.sec_classification.exchange convention: SZ=SZSE, SS=SSE). Part of PK (exchange, date). Each exchange is represented by exactly one benchmark index (see index_code).';
COMMENT ON COLUMN stats.exchange_trading_amt.index_code          IS 'Representative index code whose trading_amount is used as this exchange''s total_trading_amount on this date. Hardcoded: SZ->399001 (深证成指), SS->000001 (上证指数). FK references stats.index_identity(code, date).';
COMMENT ON COLUMN stats.exchange_trading_amt.total_trading_amount IS 'Trading turnover (yuan) of the representative index on this date. Source: stats.index_basic_stats.trading_amount. Rows where the representative index has no trading_amount (estimated-close gaps) are NOT inserted.';

-- Indexes
-- The legacy (exchange, date) index is superseded by the exchange-first
-- PK; the date-first index below replaces it.
CREATE INDEX IF NOT EXISTS idx_exchange_trading_amt_date
    ON stats.exchange_trading_amt (date);
