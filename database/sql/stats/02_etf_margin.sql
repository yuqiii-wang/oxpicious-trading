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
CREATE TABLE IF NOT EXISTS stats.etf_identity (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    code_suffix               TEXT,
    name                      TEXT          NOT NULL,

    CONSTRAINT pk_etf_identity PRIMARY KEY (date, code),
    CONSTRAINT chk_etf_identity_code_format
        CHECK (code ~ '^\d{6}\.(SZ|SS|SH)$')
);

COMMENT ON TABLE  stats.etf_identity                 IS 'ETF identity: one row per (date, etf_code). PK shared by all ETF sub-tables.';
COMMENT ON COLUMN stats.etf_identity.code           IS 'ETF ticker with exchange suffix, e.g. "159007.SZ" (SZSE) or "510050.SS" (SSE).';
COMMENT ON COLUMN stats.etf_identity.code_suffix    IS 'Exchange suffix derived from code: "SZ", "SS", or "SH". NULL if undetermined. Mirrors stock_identity.code_suffix.';

-- ----------------------------------------------------------------------------
-- Table: etf_ohlcv
--   ← Raw OHLCV (yuan)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_basic_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    prev_close                NUMERIC(18,4),
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    pct_change                NUMERIC(10,4),
    is_close_estimated        BOOLEAN       NOT NULL DEFAULT FALSE,
    has_intraday_5mins        BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_etf_basic_stats PRIMARY KEY (date, code),
    CONSTRAINT fk_etf_basic_stats_date_code FOREIGN KEY (date, code) REFERENCES stats.etf_identity(date, code)
);


COMMENT ON TABLE  stats.etf_basic_stats                    IS 'ETF raw basic_stats (yuan).';
COMMENT ON COLUMN stats.etf_basic_stats.is_close_estimated IS 'TRUE when close was estimated (not from source CSV). Estimation: for missing trading days, close is derived from prev_close adjusted by the percentage change of the most-similar index/ETF (highest composition shared weight > 60%). If no proxy qualifies, prev_close is carried forward.';
COMMENT ON COLUMN stats.etf_basic_stats.has_intraday_5mins IS 'TRUE when 5-minute intraday bars exist for this (date, code) (reserved for future ETF intraday support).';

-- ----------------------------------------------------------------------------
-- Table: etf_tech_stats
--   ← Technical indicators (moving averages)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_tech_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    ma5                       NUMERIC(18,4),
    ma5_ratio                 NUMERIC(10,6),
    ma20                      NUMERIC(18,4),
    ma60                      NUMERIC(18,4),
    ma120                     NUMERIC(18,4),
    ma255                     NUMERIC(18,4),

    CONSTRAINT pk_etf_tech_stats PRIMARY KEY (date, code),
    CONSTRAINT fk_etf_tech_stats_date_code FOREIGN KEY (date, code) REFERENCES stats.etf_identity(date, code)
);

COMMENT ON TABLE  stats.etf_tech_stats                    IS 'ETF technical indicators (moving averages).';
COMMENT ON COLUMN stats.etf_tech_stats.ma5               IS '5-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma5_ratio         IS 'Close / MA5 - 1 (ratio of price to 5-day MA).';
COMMENT ON COLUMN stats.etf_tech_stats.ma20              IS '20-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma60              IS '60-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma120             IS '120-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma255             IS '255-day moving average of adj_close.';

-- ----------------------------------------------------------------------------
-- Table: etf_adjustment
--   ← Split / dividend adjustment
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_adjustment (
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

    CONSTRAINT pk_etf_adjustment PRIMARY KEY (date, code),
    CONSTRAINT fk_etf_adjustment_date_code FOREIGN KEY (date, code) REFERENCES stats.etf_identity(date, code)
);

COMMENT ON TABLE  stats.etf_adjustment               IS 'ETF split / dividend adjustment data.';
COMMENT ON COLUMN stats.etf_adjustment.cum_split_factor IS 'Cumulative split factor (1.0 = no split). Multiply raw OHLC by 1/cum_split_factor to back-adjust.';
COMMENT ON COLUMN stats.etf_adjustment.adj_close     IS 'Split-adjusted close; the frontend uses adj_* when present, otherwise falls back to raw.';

-- ----------------------------------------------------------------------------
-- Table: etf_liquidity_margin
--   ← Liquidity and margin balances
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_liquidity_margin (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    volume_wan                NUMERIC(24,4) NOT NULL DEFAULT 0,
    amount_wan                NUMERIC(24,4) NOT NULL DEFAULT 0,
    rz_buy                    NUMERIC(24,4) NOT NULL DEFAULT 0,
    rz_balance                NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_sell_qty               NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_balance_qty            NUMERIC(24,4) NOT NULL DEFAULT 0,
    rq_balance_amt            NUMERIC(24,4) NOT NULL DEFAULT 0,
    total_balance             NUMERIC(24,4) NOT NULL DEFAULT 0,

    CONSTRAINT pk_etf_liquidity_margin PRIMARY KEY (date, code),
    CONSTRAINT fk_etf_liquidity_margin_date_code FOREIGN KEY (date, code) REFERENCES stats.etf_identity(date, code)
);

COMMENT ON TABLE  stats.etf_liquidity_margin         IS 'ETF liquidity (volume/amount) + margin balances.';
COMMENT ON COLUMN stats.etf_liquidity_margin.rz_balance IS '融资余额 (yuan) — borrowed cash to buy the ETF; always non-negative.';
COMMENT ON COLUMN stats.etf_liquidity_margin.rq_balance_amt IS '融券余额 (yuan) — borrowed ETF value outstanding; SSE source computes as 融券余量 × (open+close)/2 mid price.';
COMMENT ON COLUMN stats.etf_liquidity_margin.total_balance IS 'rz_balance + rq_balance_amt — total margin outstanding.';

-- ----------------------------------------------------------------------------
-- Table: etf_composition_link  (REMOVED)
--   Was a write-only mirror of an in-memory merge_asof comp_match_date column.
--   No reader ever queried it; the composition pipeline now writes directly
--   to sec_composition. Dropped for cleanup; the v_etf_margin view no longer
--   LEFT JOINs it.
--   NOTE: v_etf_margin is dropped first because the old view referenced this
--   table; it is recreated in 99_reconstruct_views.sql without the JOIN.
-- ----------------------------------------------------------------------------
DROP VIEW IF EXISTS stats.v_etf_margin;
DROP TABLE IF EXISTS stats.etf_composition_link;

-- Indexes
DROP INDEX IF EXISTS stats.idx_etf_margin_code_date;

-- (a) Per-code lookups (latest first); INCLUDE (name, code_suffix) lets the
--     Index Only Scan return these columns without heap fetches. Mirrors
--     idx_stock_identity_code_date.
CREATE INDEX IF NOT EXISTS idx_etf_identity_code_date
    ON stats.etf_identity (code, date DESC) INCLUDE (name, code_suffix);

-- (b) Exchange-filtered lookups — WHERE code_suffix='SZ'/'SS', then by code.
--     Supports check_identity(code_suffix=...) and listing all ETFs of one
--     exchange with latest-name-per-code via DISTINCT ON (code) ...
--     ORDER BY code, date DESC. Mirrors idx_stock_identity_suffix_code_date.
CREATE INDEX IF NOT EXISTS idx_etf_identity_suffix_code_date
    ON stats.etf_identity (code_suffix, code, date DESC);

CREATE INDEX IF NOT EXISTS idx_etf_basic_stats_code_date
    ON stats.etf_basic_stats (code, date);

CREATE INDEX IF NOT EXISTS idx_etf_tech_stats_code_date
    ON stats.etf_tech_stats (code, date);

CREATE INDEX IF NOT EXISTS idx_etf_adjustment_code_date
    ON stats.etf_adjustment (code, date);

CREATE INDEX IF NOT EXISTS idx_etf_liquidity_margin_code_date
    ON stats.etf_liquidity_margin (code, date);

CREATE INDEX IF NOT EXISTS idx_etf_margin_split_events
    ON stats.etf_adjustment (date)
    WHERE is_split_event_day = 1;