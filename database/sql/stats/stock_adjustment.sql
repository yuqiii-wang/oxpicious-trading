-- ============================================================================
--  Stock Margin + Adjustment - Mirror of ETF family
--  Source CSVs: szse_margin_detail_*.csv + sse_margin_detail_*.csv (filtered
--  to STOCK codes — ETF prefixes are excluded; loaded by builds.etf).
--
--  Split into: stock_adjustment (corp-action adj OHLC) + stock_margin (RZ/RQ)
--  Reconstruct via: v_stock_daily view (see 99_reconstruct_views.sql —
--  extended here to LEFT JOIN the two new tables).
--
--  Schema mirrors the ETF family (the etf_*.sql files) for symmetry:
--    stock_adjustment ~ etf_adjustment       (cum_split_factor, adj_*)
--    stock_margin      ~ etf_liquidity_margin (rz_*, rq_*, total_balance —
--                                             trading_shares/trading_amount
--                                             stay in stock_basic_stats)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: stock_adjustment
--   ← Split / dividend adjustment (mirrors etf_adjustment)
--   Populated by builds.stock.margin from full per-code OHLCV history
--   (queried from stock_basic_stats) via the shared apply_split_adjustment()
--   algorithm imported from builds.etf.__main__.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each stock's split and dividend adjustments that splice distributions back into its price series.
-- tags: stock, 股票, adjusted price, 复权, dividend, 分红

CREATE TABLE IF NOT EXISTS stats.stock_adjustment (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    cum_split_factor          NUMERIC(18,8) NOT NULL DEFAULT 1.0,
    is_split_event_day        SMALLINT     NOT NULL DEFAULT 0
        CHECK (is_split_event_day IN (0,1)),
    action_type               TEXT,
    implied_dividend_per_share NUMERIC(18,6) NOT NULL DEFAULT 0,
    cum_dividend_per_share    NUMERIC(18,6) NOT NULL DEFAULT 0,
    adj_prev_close            NUMERIC(18,6),
    adj_open                  NUMERIC(18,6),
    adj_high                  NUMERIC(18,6),
    adj_low                   NUMERIC(18,6),
    adj_close                 NUMERIC(18,6),

    CONSTRAINT pk_stock_adjustment PRIMARY KEY (code, date),
    CONSTRAINT fk_stock_adjustment_date_code FOREIGN KEY (code, date) REFERENCES stats.stock_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'stock_adjustment', 8);

COMMENT ON TABLE  stats.stock_adjustment               IS 'Stock split / dividend adjustment data. Mirrors stats.etf_adjustment.';
COMMENT ON COLUMN stats.stock_adjustment.cum_split_factor IS 'Cumulative split factor (1.0 = no split). Multiply raw OHLC by 1/cum_split_factor to back-adjust.';
COMMENT ON COLUMN stats.stock_adjustment.adj_close     IS 'Split-adjusted close; the frontend uses adj_* when present, otherwise falls back to raw.';

-- Legacy (code, date) index is redundant with the code-first PK — replaced by
-- a date-first index.
CREATE INDEX IF NOT EXISTS idx_stock_adjustment_date
    ON stats.stock_adjustment (date);

CREATE INDEX IF NOT EXISTS idx_stock_adjustment_split_events
    ON stats.stock_adjustment (date)
    WHERE is_split_event_day = 1;
