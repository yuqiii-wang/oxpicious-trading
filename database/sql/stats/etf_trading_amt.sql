
-- ----------------------------------------------------------------------------
-- Table: etf_trading_amt
--   Per-(date, industry_id) aggregate ETF trading turnover.
--   `code` is an industry_id (e.g. BANKS, SEMI, BROAD_CSI) — aggregates ALL
--   ETFs whose linked parent index (stats.sec_classification.parent_index_code)
--   carries that industry classification. Built by build_index_exts.py from
--   the same etf_liquidity_margin source as index_exts, but grouped by the
--   linked index's industry_id instead of by index code.
--   Consumed by the Perf-Attr "Industry Trading Amt contribution" chart.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Aggregate ETF turnover per industry per day — how much ETF money trades each theme.
-- tags: etf, turnover, 成交额, industry, 行业

CREATE TABLE IF NOT EXISTS stats.etf_trading_amt (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    etf_num                   INTEGER,
    total_etf_trading_amount  NUMERIC(18,4),
    total_etf_trading_amount_ma5 NUMERIC(18,4),

    CONSTRAINT pk_etf_trading_amt PRIMARY KEY (code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code (industry_id)
-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_trading_amt', 8);


COMMENT ON TABLE  stats.etf_trading_amt                  IS 'Per-(date, industry_id) aggregate ETF trading turnover. `code` is an industry_id (e.g. BANKS, SEMI, BROAD_CSI) — aggregates ALL ETFs whose linked parent index (stats.sec_classification.parent_index_code) carries that industry classification. Sourced via etf_liquidity_margin JOIN sec_classification(etf→parent_index_code) JOIN sec_classification(index→industry_id).';
COMMENT ON COLUMN stats.etf_trading_amt.code             IS 'Industry id (e.g. BANKS, SEMI, BROAD_CSI). Maps to stats.sec_classification.industry_id of the ETF''s linked parent index (parent_index_code → index''s industry_id). NOT an index code — industry ids are alpha strings, distinct from 6-digit index codes.';
COMMENT ON COLUMN stats.etf_trading_amt.etf_num          IS 'Number of ETFs whose linked parent index carries this industry_id on this date. Source: COUNT(DISTINCT etf_liquidity_margin.code).';
COMMENT ON COLUMN stats.etf_trading_amt.total_etf_trading_amount    IS 'Aggregate ETF trading turnover (yuan) on this date across ALL ETFs whose linked parent index has this industry_id. Source: Σ etf_liquidity_margin.trading_amount. Consumed by the Perf-Attr "Industry Trading Amt contribution" chart.';
COMMENT ON COLUMN stats.etf_trading_amt.total_etf_trading_amount_ma5 IS '5-trading-day moving average of total_etf_trading_amount (AVG over the trailing 5 rows per code ordered by date). NULL for the first 4 rows of a code''s history.';

CREATE INDEX IF NOT EXISTS idx_etf_trading_amt_date
    ON stats.etf_trading_amt (date);
