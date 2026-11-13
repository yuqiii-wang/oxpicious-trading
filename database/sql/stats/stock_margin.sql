
-- ----------------------------------------------------------------------------
-- Table: stock_margin
--   ← Margin balances (RZ/RQ) — mirror of etf_liquidity_margin
--   NOTE: trading_shares/trading_amount live in stock_basic_stats (already
--   populated by builds.stock.__main__). Only margin-specific fields are
--   stored here to avoid duplicating the trading_* columns.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each stock's margin-trading and short-selling balances (融资融券).
-- tags: stock, 股票, margin, 融资融券

CREATE TABLE IF NOT EXISTS stats.stock_margin (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    rz_buy                    NUMERIC(24,4) NOT NULL DEFAULT 0,
    rz_balance                NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_sell_qty               NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_balance_qty            NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_balance_amt            NUMERIC(24,4) NOT NULL DEFAULT 0,
    total_balance             NUMERIC(24,4) NOT NULL DEFAULT 0,

    CONSTRAINT pk_stock_margin PRIMARY KEY (code, date),
    CONSTRAINT fk_stock_margin_date_code FOREIGN KEY (code, date) REFERENCES stats.stock_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'stock_margin', 8);

COMMENT ON TABLE  stats.stock_margin              IS 'Stock margin balances (融资融券). Mirrors stats.etf_liquidity_margin margin fields; trading_shares/trading_amount stay in stock_basic_stats.';
COMMENT ON COLUMN stats.stock_margin.rz_balance   IS '融资余额 (yuan) — borrowed cash to buy the stock; always non-negative.';
COMMENT ON COLUMN stats.stock_margin.rq_balance_amt IS '融券余额 (yuan) — borrowed stock value outstanding; SSE source computes as 融券余量 × (open+close)/2 mid price when missing.';
COMMENT ON COLUMN stats.stock_margin.total_balance IS 'rz_balance + rq_balance_amt — total margin outstanding.';

-- Legacy (code, date) index is redundant with the code-first PK — replaced by
-- a date-first index.
CREATE INDEX IF NOT EXISTS idx_stock_margin_date
    ON stats.stock_margin (date);

-- v_stock_daily: authoritative definition lives in 99_reconstruct_views.sql
-- (JOIN of stock_identity + stock_basic_stats + stock_tech_stats +
-- stock_liquidity_margin). The earlier draft view block here was removed —
-- it referenced stock_basic_stats.trading_shares, which actually lives in
-- stock_liquidity_margin, and so could not be created.
