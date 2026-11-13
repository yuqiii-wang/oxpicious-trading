
-- ----------------------------------------------------------------------------
-- Table: stock_liquidity_margin
--   ← Liquidity (trading_shares/trading_amount) + margin balances (融资融券).
--   Mirrors stats.etf_liquidity_margin. trading_shares/trading_amount
--   previously lived on stock_basic_stats; they were moved here so the stock
--   family mirrors the ETF family split (basic_stats = OHLCV+PE only;
--   liquidity_margin = liquidity + margin).
--
--   Source: builds/stock/__main__.py reads SZSE + SSE margin detail CSVs
--   (temps/{szse,sse}_margin/{szse,sse}_margin_detail_YYYYMMDD.csv), filtering
--   to STOCK codes (excludes ETF prefixes 510xxx/511xxx/.../159xxx/150xxx).
--   SSE margin detail CSVs contain BOTH ETFs and stocks (the underlying API
--   returns all SSE-listed margin-eligible securities); the filter keeps only
--   the stock rows. SZSE margin detail CSVs likewise contain only stocks
--   (SZSE ETFs are not margin-eligible, so they don't appear there).
--
--   The 6 margin columns are NULLABLE: NULL = no margin data ingested for
--   this (date, code) — no CSV row, or the file was missing/empty at build
--   time (the margin gap detector re-reads the date once the CSV lands).
--   0 is a REAL zero reported by the exchange. NOT NULL DEFAULT 0 baked
--   indistinguishable whole-market zero rows on download-failure dates
--   that poisoned the margin analysis (rz_balance > 0 filters) — the
--   2026-09/10 margin gap. trading_shares/trading_amount stay NOT NULL
--   (OHLCV-owned; 0 = suspended / no volume).
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each stock's daily traded shares and amount plus its margin-trading balances.
-- tags: stock, 股票, trading amount, 成交额, margin, 融资融券

CREATE TABLE IF NOT EXISTS stats.stock_liquidity_margin (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    trading_shares            NUMERIC(24,4) NOT NULL DEFAULT 0,
    trading_amount            NUMERIC(24,4) NOT NULL DEFAULT 0,
    rz_buy                    NUMERIC(24,4),
    rz_balance                NUMERIC(24,4),
    rq_sell_qty               NUMERIC(24,4),
    rq_balance_qty            NUMERIC(24,4),
    rq_balance_amt            NUMERIC(24,4),
    total_balance             NUMERIC(24,4),

    CONSTRAINT pk_stock_liquidity_margin PRIMARY KEY (code, date),
    CONSTRAINT fk_stock_liquidity_margin_date_code FOREIGN KEY (code, date) REFERENCES stats.stock_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('stats', 'stock_liquidity_margin', 16);

COMMENT ON TABLE  stats.stock_liquidity_margin                   IS 'Stock liquidity (trading_shares/trading_amount) + margin balances. Mirrors stats.etf_liquidity_margin.';
COMMENT ON COLUMN stats.stock_liquidity_margin.trading_shares    IS 'Stock trading volume in SHARES. Source CSV stores 成交量(万股); converted to shares (× 10000) in builds/stock/__main__.py. NULL for PE-only rows (no OHLCV source) — those rows are NOT inserted into this table.';
COMMENT ON COLUMN stats.stock_liquidity_margin.trading_amount    IS 'Stock trading turnover in yuan. Source CSV stores 成交金额(万元); converted to yuan (× 10000). Used by analyze_industry_sentiments.py to compute total industry capital flow (SUM across union of member-index stocks).';
COMMENT ON COLUMN stats.stock_liquidity_margin.rz_balance        IS '融资余额 (yuan) — borrowed cash to buy the stock; always non-negative. Source: SZSE + SSE margin detail CSVs.';
COMMENT ON COLUMN stats.stock_liquidity_margin.rq_balance_amt    IS '融券余额 (yuan) — borrowed stock value outstanding. SSE detail CSV does NOT publish this column (only 融券余量 qty); it is 0 for SSE stocks. SZSE detail CSV publishes it directly.';
COMMENT ON COLUMN stats.stock_liquidity_margin.total_balance     IS 'rz_balance + rq_balance_amt — total margin outstanding. SSE detail CSV does NOT publish this column; it is rz_balance + 0 for SSE stocks. SZSE detail CSV publishes it directly.';

CREATE INDEX IF NOT EXISTS idx_stock_liquidity_margin_date
    ON stats.stock_liquidity_margin (date);

