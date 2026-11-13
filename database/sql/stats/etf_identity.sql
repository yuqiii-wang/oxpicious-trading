-- ============================================================================
--  ETF Margin - Split Tables
--  Original: etf_margin table from schema.sql (fixed naming: etf_trend_and_margin → etf_margin)
--  Split into: etf_identity, etf_ohlcv, etf_adjustment, etf_liquidity_margin
--  Reconstruct via: v_etf_margin view (see 99_reconstruct_views.sql)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: etf_identity
--   Identity core (PK) for all etf_margin sub-tables
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Which ETF traded on which day — the shared identity for all ETF daily tables.
-- tags: etf, identity, 标识, reference, 参考

CREATE TABLE IF NOT EXISTS stats.etf_identity (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    exchange                  TEXT,
    name                      TEXT          NOT NULL,

    CONSTRAINT pk_etf_identity PRIMARY KEY (code, date),
    CONSTRAINT chk_etf_identity_code_format
        CHECK (code ~ '^\d{6}$')
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_identity', 8);

COMMENT ON TABLE  stats.etf_identity                 IS 'ETF identity: one row per (date, etf_code). PK (code, date) shared by all ETF sub-tables. Native HASH partitioned by code.';
COMMENT ON COLUMN stats.etf_identity.code           IS 'ETF/fund code, BARE 6-digit (e.g. "159007" SZSE, "510050" SSE) — the exchange suffix was removed 2026-09-30 (stocks keep theirs). OTC ETF feeder funds (联接基金, stats.sec_etf_links.feeder_code, loaded by builds.etf.feeder_nav) live here bare as well.';
COMMENT ON COLUMN stats.etf_identity.exchange       IS 'Listing venue: "SS" or "SZ" for exchange-listed ETFs, carried from the canonical source-CSV exchange column; NULL for OTC feeder funds (no listing). Authoritative listed-vs-OTC discriminator — never derive it from the code (the code is bare).';


-- Indexes

-- (a) Per-code lookups (latest first); INCLUDE (name, exchange) lets the
--     Index Only Scan return these columns without heap fetches. Mirrors
--     idx_stock_identity_code_date. (Plain (code, date) prefix is now served
--     by the PK (code, date) itself.)
CREATE INDEX IF NOT EXISTS idx_etf_identity_code_date
    ON stats.etf_identity (code, date DESC) INCLUDE (name, exchange);


-- Date-first scans across all codes (the old date-first PK served these;
-- PK is now (code, date) so an explicit date index restores the pattern).
CREATE INDEX IF NOT EXISTS idx_etf_identity_date
    ON stats.etf_identity (date);


-- (b) Exchange-filtered lookups — WHERE exchange='SZ'/'SS', then by code.
--     Supports check_identity(exchange=...) and listing all ETFs of one
--     exchange with latest-name-per-code via DISTINCT ON (code) ...
--     ORDER BY code, date DESC. Mirrors idx_stock_identity_exchange_code_date.
CREATE INDEX IF NOT EXISTS idx_etf_identity_exchange_code_date
    ON stats.etf_identity (exchange, code, date DESC);
