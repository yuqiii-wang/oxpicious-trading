-- ============================================================================
--  Stock Dividends (利润分配/分红) — SSE per-stock dividend history
--  Source: downloads/stock/sse/dividend/__main__.py
--    (SSE commonQuery.do, sqlId COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L)
--  Loaded via: builds/stock/__main__.py dividend subcommand
--    (reads {code}_dividend.csv files from temps/sse_archive/)
--
--  One row per dividend event, identified by (code, ex_dividend_date).
--  Ex-dividend date is the natural unique key — it is the date the stock
--  starts trading without the right to the dividend, and SSE only ever
--  publishes one dividend event per ex-dividend date per stock.
--
--  Notes:
--    • announcement_date is left NULL for SSE — the SSE 分红 API does NOT
--      return 公告日期. The column exists for forward compatibility (SZSE
--      and other sources may populate it later).
--    • All per-share amounts are in yuan (per single share, NOT per 10 shares
--      — the SSE API already returns per-share values).
--    • total_dividend is stored in 万元 (the SSE API's native unit) — the
--      "wan" suffix is kept in the column name for clarity.
--    • total_shares is in 万股 (the SSE API's native unit).
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.stock_dividends (
    code                          TEXT          NOT NULL,
    name                          TEXT,
    announcement_date             DATE,
    record_date                   DATE,
    ex_dividend_date              DATE          NOT NULL,
    dividend_per_share_pre_tax    NUMERIC(18,6),
    dividend_per_share_post_tax   NUMERIC(18,6),
    total_dividend_wan            NUMERIC(24,4),
    pre_close_price               NUMERIC(18,4),
    open_price                    NUMERIC(18,4),
    total_shares_wan              NUMERIC(24,4),
    source                        TEXT          NOT NULL DEFAULT 'SSE',
    last_updated                  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_stock_dividends PRIMARY KEY (code, ex_dividend_date),
    CONSTRAINT chk_stock_dividends_code_format
        CHECK (code ~ '^\d{6}\.(SZ|SS|BJ|HK)$'),
    CONSTRAINT chk_stock_dividends_source
        CHECK (source IN ('SSE', 'SZSE', 'BSE', 'HK', 'MANUAL'))
);


COMMENT ON TABLE  stats.stock_dividends                              IS 'Per-stock dividend (利润分配/分红) history. One row per (code, ex_dividend_date). Source: SSE commonQuery.do (sqlId COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L) loaded from {code}_dividend.csv files in temps/sse_archive/. Used by the stock OHLC chart to mark ex-dividend events.';
COMMENT ON COLUMN stats.stock_dividends.code                          IS 'Stock ticker with exchange suffix, e.g. "600008.SS" (首创环保). Matches the format used in stats.stock_identity.code.';
COMMENT ON COLUMN stats.stock_dividends.name                          IS 'Stock short name (证券简称). May be NULL when source CSV omits it.';
COMMENT ON COLUMN stats.stock_dividends.announcement_date             IS 'Announcement date (公告日期). NULL for SSE — the SSE 分红 API does not return this field. Populated when source = SZSE/BSE.';
COMMENT ON COLUMN stats.stock_dividends.record_date                   IS 'Share registration date (股权登记日) — last day to buy the stock and still receive the dividend.';
COMMENT ON COLUMN stats.stock_dividends.ex_dividend_date              IS 'Ex-dividend date (除息交易日) — first day the stock trades without the right to the dividend. Part of the PK because SSE only ever publishes one dividend event per ex-dividend date per stock.';
COMMENT ON COLUMN stats.stock_dividends.dividend_per_share_pre_tax    IS 'Dividend per share, pre-tax (每股红利含税), in yuan per single share. SSE API returns per-share values (not per-10-share).';
COMMENT ON COLUMN stats.stock_dividends.dividend_per_share_post_tax   IS 'Dividend per share, post-tax (每股红利税后), in yuan per single share.';
COMMENT ON COLUMN stats.stock_dividends.total_dividend_wan            IS 'Total dividend payout (分红总额), in 万元 (10,000 yuan). SSE API native unit.';
COMMENT ON COLUMN stats.stock_dividends.pre_close_price               IS 'Closing price on the day BEFORE ex-dividend (除息前日收盘价), in yuan.';
COMMENT ON COLUMN stats.stock_dividends.open_price                    IS 'Ex-dividend opening quote (除息报价), in yuan. Theorised open = pre_close - dividend_per_share_pre_tax.';
COMMENT ON COLUMN stats.stock_dividends.total_shares_wan              IS 'Total shares outstanding on the record date (股权登记日总股本), in 万股 (10,000 shares). SSE API native unit.';
COMMENT ON COLUMN stats.stock_dividends.source                        IS 'Data source: SSE (default) / SZSE / BSE / HK / MANUAL.';
COMMENT ON COLUMN stats.stock_dividends.last_updated                  IS 'When this row was last upserted. DEFAULT NOW() — updated automatically by every bulk upsert.';

-- ----------------------------------------------------------------------------
-- Indexes
--   (a) code-ascending: dominant access pattern — UI stock OHLC chart
--       queries all dividends for a single code to overlay as event markers.
--   (b) ex_dividend_date-ascending: cross-stock queries by date (e.g. "all
--       dividends announced today") — useful for screener-style pages.
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_stock_dividends_code_exdate
    ON stats.stock_dividends (code, ex_dividend_date);

CREATE INDEX IF NOT EXISTS idx_stock_dividends_exdate
    ON stats.stock_dividends (ex_dividend_date);
