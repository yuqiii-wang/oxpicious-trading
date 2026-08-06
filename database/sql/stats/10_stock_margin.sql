-- ============================================================================
--  Stock Margin + Adjustment - Mirror of ETF family
--  Source CSVs: szse_margin_detail_*.csv + sse_margin_detail_*.csv (filtered
--  to STOCK codes — ETF prefixes are excluded; loaded by builds.etf).
--
--  Split into: stock_adjustment (corp-action adj OHLC) + stock_margin (RZ/RQ)
--  Reconstruct via: v_stock_baseline view (see 99_reconstruct_views.sql —
--  extended here to LEFT JOIN the two new tables).
--
--  Schema mirrors the ETF family (02_etf_margin.sql) for symmetry:
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

    CONSTRAINT pk_stock_adjustment PRIMARY KEY (date, code),
    CONSTRAINT fk_stock_adjustment_date_code FOREIGN KEY (date, code) REFERENCES stats.stock_identity(date, code)
);

COMMENT ON TABLE  stats.stock_adjustment               IS 'Stock split / dividend adjustment data. Mirrors stats.etf_adjustment.';
COMMENT ON COLUMN stats.stock_adjustment.cum_split_factor IS 'Cumulative split factor (1.0 = no split). Multiply raw OHLC by 1/cum_split_factor to back-adjust.';
COMMENT ON COLUMN stats.stock_adjustment.adj_close     IS 'Split-adjusted close; the frontend uses adj_* when present, otherwise falls back to raw.';

CREATE INDEX IF NOT EXISTS idx_stock_adjustment_code_date
    ON stats.stock_adjustment (code, date);

CREATE INDEX IF NOT EXISTS idx_stock_adjustment_split_events
    ON stats.stock_adjustment (date)
    WHERE is_split_event_day = 1;

-- ----------------------------------------------------------------------------
-- Table: stock_margin
--   ← Margin balances (RZ/RQ) — mirror of etf_liquidity_margin
--   NOTE: trading_shares/trading_amount live in stock_basic_stats (already
--   populated by builds.stock.__main__). Only margin-specific fields are
--   stored here to avoid duplicating the trading_* columns.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.stock_margin (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    rz_buy                    NUMERIC(24,4) NOT NULL DEFAULT 0,
    rz_balance                NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_sell_qty               NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_balance_qty            NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_balance_amt            NUMERIC(24,4) NOT NULL DEFAULT 0,
    total_balance             NUMERIC(24,4) NOT NULL DEFAULT 0,

    CONSTRAINT pk_stock_margin PRIMARY KEY (date, code),
    CONSTRAINT fk_stock_margin_date_code FOREIGN KEY (date, code) REFERENCES stats.stock_identity(date, code)
);

COMMENT ON TABLE  stats.stock_margin              IS 'Stock margin balances (融资融券). Mirrors stats.etf_liquidity_margin margin fields; trading_shares/trading_amount stay in stock_basic_stats.';
COMMENT ON COLUMN stats.stock_margin.rz_balance   IS '融资余额 (yuan) — borrowed cash to buy the stock; always non-negative.';
COMMENT ON COLUMN stats.stock_margin.rq_balance_amt IS '融券余额 (yuan) — borrowed stock value outstanding; SSE source computes as 融券余量 × (open+close)/2 mid price when missing.';
COMMENT ON COLUMN stats.stock_margin.total_balance IS 'rz_balance + rq_balance_amt — total margin outstanding.';

CREATE INDEX IF NOT EXISTS idx_stock_margin_code_date
    ON stats.stock_margin (code, date);

-- ----------------------------------------------------------------------------
-- View: v_stock_baseline (extended)
--   DROP + recreate to LEFT JOIN the two new tables.
--   Mirrors v_etf_margin structure (identity + basic_stats + adjustment +
--   tech_stats + margin).
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS stats.v_stock_baseline;
CREATE OR REPLACE VIEW stats.v_stock_baseline AS
SELECT
    i.date,
    i.code,
    i.name,
    -- Raw OHLC + pct_change (mirrors etf_basic_stats)
    b.prev_close,
    b.open,
    b.high,
    b.low,
    b.close,
    b.pct_change,
    b.has_intraday_5mins,
    -- Stock-specific valuation
    b.pe,
    b.is_pe_estimated,
    -- Trading liquidity (already in stock_basic_stats — kept here for parity
    -- with v_etf_margin so the frontend can read trading_shares/amount from
    -- the same view).
    b.trading_shares,
    b.trading_amount,
    -- Adjustment (mirror of etf_adjustment)
    a.cum_split_factor,
    a.is_split_event_day,
    a.action_type,
    a.implied_dividend_per_share,
    a.cum_dividend_per_share,
    a.adj_prev_close,
    a.adj_open,
    a.adj_high,
    a.adj_low,
    a.adj_close,
    -- Technical (mirror of etf_tech_stats — already exists as stock_tech_stats)
    t.ma5,
    t.ma5_ratio,
    t.ma20,
    t.ma60,
    t.ma120,
    t.ma255,
    -- Margin (mirror of etf_liquidity_margin margin fields)
    m.rz_buy,
    m.rz_balance,
    m.rq_sell_qty,
    m.rq_balance_qty,
    m.rq_balance_amt,
    m.total_balance
FROM stats.stock_identity i
LEFT JOIN stats.stock_basic_stats b ON i.date = b.date AND i.code = b.code
LEFT JOIN stats.stock_adjustment  a ON i.date = a.date AND i.code = a.code
LEFT JOIN stats.stock_tech_stats  t ON i.date = t.date AND i.code = t.code
LEFT JOIN stats.stock_margin      m ON i.date = m.date AND i.code = m.code;

COMMENT ON VIEW stats.v_stock_baseline IS 'Reconstructed stock_baseline view: JOIN of stock_identity + stock_basic_stats + stock_adjustment + stock_tech_stats + stock_margin. Mirrors v_etf_margin structure.';
