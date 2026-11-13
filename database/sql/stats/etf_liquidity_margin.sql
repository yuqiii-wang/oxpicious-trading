
-- ----------------------------------------------------------------------------
-- Table: etf_liquidity_margin
--   ← Liquidity and margin balances
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each ETF's daily traded shares and amount plus its margin-trading balances.
-- tags: etf, trading amount, 成交额, margin, 融资融券

CREATE TABLE IF NOT EXISTS stats.etf_liquidity_margin (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    trading_shares            NUMERIC(24,4) NOT NULL DEFAULT 0,
    trading_amount            NUMERIC(24,4) NOT NULL DEFAULT 0,
    -- The 6 margin columns are NULLABLE: NULL = no margin data ingested for
    -- this (date, code) — no CSV row, or the file was missing/empty at
    -- build time (the margin-stale repair re-reads it once the CSV lands).
    -- 0 is a REAL zero reported by the exchange. NOT NULL DEFAULT 0 baked
    -- indistinguishable whole-market zero rows that poisoned the margin
    -- analysis (rz_balance > 0 filters) — the 2026-09/10 margin gap.
    rz_buy                    NUMERIC(24,4),
    rz_balance                NUMERIC(24,4),
    rq_sell_qty               NUMERIC(24,4),
    rq_balance_qty            NUMERIC(24,4),
    rq_balance_amt            NUMERIC(24,4),
    total_balance             NUMERIC(24,4),

    CONSTRAINT pk_etf_liquidity_margin PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_liquidity_margin_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_liquidity_margin', 8);

COMMENT ON TABLE  stats.etf_liquidity_margin         IS 'ETF liquidity (trading_shares/trading_amount) + margin balances.';
COMMENT ON COLUMN stats.etf_liquidity_margin.trading_shares IS 'ETF trading volume in shares. Source CSV stores 成交量(万股); converted to shares (× 10000) in builds/etf/__main__.py.';
COMMENT ON COLUMN stats.etf_liquidity_margin.trading_amount IS 'ETF trading turnover in yuan. Source CSV stores 成交金额(万元); converted to yuan (× 10000) in builds/etf/__main__.py. A 1000x error normalization fix is applied before conversion (see build script).';
COMMENT ON COLUMN stats.etf_liquidity_margin.rz_balance IS '融资余额 (yuan) — borrowed cash to buy the ETF; always non-negative.';
COMMENT ON COLUMN stats.etf_liquidity_margin.rq_balance_amt IS '融券余额 (yuan) — borrowed ETF value outstanding; SSE source computes as 融券余量 × (open+close)/2 mid price.';
COMMENT ON COLUMN stats.etf_liquidity_margin.total_balance IS 'rz_balance + rq_balance_amt — total margin outstanding.';

CREATE INDEX IF NOT EXISTS idx_etf_liquidity_margin_date
    ON stats.etf_liquidity_margin (date);
