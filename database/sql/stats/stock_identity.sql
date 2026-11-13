-- ============================================================================
--  Stock Daily - Split Tables
--  Source: build_szse_sse_bse_stocks.py (from SZSE archive/trend + SSE trend + BSE trend CSVs)
--  Split into: stock_identity, stock_basic_stats, stock_liquidity_margin
--  Reconstruct via: v_stock_daily view (see 99_reconstruct_views.sql)
--
--  Schema mirrors the ETF family (the etf_*.sql files) for symmetry:
--    stock_identity          ~  etf_identity          (date, code, name)
--    stock_basic_stats       ~  etf_basic_stats       (date, code, prev_close, open,
--                                                      high, low, close, pct_change)
--    stock_liquidity_margin  ~  etf_liquidity_margin  (trading_shares, trading_amount,
--                                                      rz_*, rq_*, total_balance)
--  stock_basic_stats additionally keeps `pe` (stock-specific valuation).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: stock_identity
--   Identity core (PK) for all stock sub-tables
--   Mirrors etf_identity (date, code, name)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Which stock traded on which day — the shared identity for all stock daily tables.
-- tags: stock, 股票, identity, 标识, reference, 参考

CREATE TABLE IF NOT EXISTS stats.stock_identity (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    exchange                  TEXT,
    board                     SMALLINT,  -- label code (static.label_dim 'board'): 0=MAIN 1=STAR 2=GEM 3=BSE; NULL when the source row carries no board
    name                      TEXT          NOT NULL DEFAULT '',
    is_in_index_or_etf        BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_stock_identity PRIMARY KEY (code, date),
    CONSTRAINT chk_stock_identity_code_format
        CHECK (code ~ '^\d{6}\.(SZ|SS|BJ)$')
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('stats', 'stock_identity', 16);

COMMENT ON TABLE  stats.stock_identity            IS 'Stock identity: one row per (date, code). PK (code, date) shared by all stock sub-tables. Native HASH partitioned by code. Mirrors etf_identity.';
COMMENT ON COLUMN stats.stock_identity.code       IS 'Stock ticker with exchange suffix, e.g. "000001.SZ" (Ping An Bank) or "600000.SS" (Pudong Development Bank).';
COMMENT ON COLUMN stats.stock_identity.exchange   IS 'Exchange of the code suffix: "SZ", "SS", or "BJ". Carried from the canonical source-CSV exchange column; replaces the legacy code_suffix column.';
COMMENT ON COLUMN stats.stock_identity.board      IS 'Listing board as its static.label_dim (''board'' domain) SMALLINT code: 0 = MAIN, 1 = STAR (科创板 688/689.SS), 2 = GEM (创业板 30xxxx.SZ), 3 = BSE (Beijing). Carried from the canonical source-CSV board column (mapped at the write boundary); NULL when the source row carries no board.';
COMMENT ON COLUMN stats.stock_identity.is_in_index_or_etf  IS 'TRUE when this stock appears in any ETF or index composition (stats.sec_composition source_type in etf/index). Populated by populate_is_in_etf.py; used by stream_szse_price.py to avoid runtime EXISTS subquery.';


-- ----------------------------------------------------------------------------
-- Indexes for stock_identity
--   stock_identity is large (~3M+ rows, one per (date, code)) and serves as the
--   FK parent for all stock sub-tables. The dominant access patterns are:
--     (a) latest name/stats for one code:   WHERE code=$1 ORDER BY date DESC LIMIT 1
--     (b) all stocks of one exchange:         WHERE exchange='SZ' (or 'SS')
--     (c) bulk join by (date, code):         handled by the PK
-- ----------------------------------------------------------------------------

-- (a) Covering index for latest-per-code lookups — the dominant pattern used by
--     stream_szse_price.py / stream_sse_price.py load_target_stocks (LATERAL
--     ... ORDER BY date DESC LIMIT 1) and single-code name resolutions.
--     date DESC matches the "latest first" ordering so the planner can stop
--     after LIMIT 1 without a sort; INCLUDE (name, exchange) lets the
--     Index Only Scan return these columns without heap fetches.
CREATE INDEX IF NOT EXISTS idx_stock_identity_code_date
    ON stats.stock_identity (code, date DESC) INCLUDE (name, exchange);


-- Date-first scans across all codes (the old date-first PK served these;
-- PK is now (code, date) so an explicit date index restores the pattern).
CREATE INDEX IF NOT EXISTS idx_stock_identity_date
    ON stats.stock_identity (date);


-- (b) Exchange-filtered lookups — WHERE exchange='SZ'/'SS', then by code.
--     Supports listing all stocks of one exchange with latest-name-per-code
--     via DISTINCT ON (code) ... ORDER BY code, date DESC.
--     INCLUDE (name, is_in_index_or_etf) lets the Index Only Scan return these
--     columns without heap fetches (stream_szse_price.py filters on is_in_index_or_etf).
CREATE INDEX IF NOT EXISTS idx_stock_identity_exchange_code_date
    ON stats.stock_identity (exchange, code, date DESC) INCLUDE (name, is_in_index_or_etf);
