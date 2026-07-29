-- ============================================================================
--  Index Baseline - Split Tables
--  Original: CSIndex daily history + intraday tick data
--  Split into: index_identity, index_basic_stats, index_valuation,
--              index_tech_stats, index_intraday_5min
--  Reconstruct via: v_index_baseline view (see 99_reconstruct_views.sql)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: index_identity
--   Identity core (PK) for all index sub-tables
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.index_identity (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    name                      TEXT          NOT NULL,

    CONSTRAINT pk_index_identity PRIMARY KEY (date, code),
    CONSTRAINT chk_index_identity_code_format
        CHECK (code ~ '^(\d{6}|H\d{5})$')
);

COMMENT ON TABLE  stats.index_identity                 IS 'Index identity: one row per (date, index_code). PK shared by all index sub-tables.';
COMMENT ON COLUMN stats.index_identity.code            IS 'Index code, e.g. "000300" (CSI300), "H30007" (chip industry).';

-- ----------------------------------------------------------------------------
-- Table: index_basic_stats
--   ← Daily OHLCV + volume + amount + change metrics
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.index_basic_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    volume                    NUMERIC(24,4),
    amount                    NUMERIC(24,4),
    change                     NUMERIC(18,4),
    change_pct                 NUMERIC(10,4),
    has_intraday_5mins         BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_index_basic_stats PRIMARY KEY (date, code),
    CONSTRAINT fk_index_basic_stats_date_code FOREIGN KEY (date, code) REFERENCES stats.index_identity(date, code)
);


COMMENT ON TABLE  stats.index_basic_stats                    IS 'Index daily OHLCV + volume + amount + change metrics.';
COMMENT ON COLUMN stats.index_basic_stats.volume             IS 'Index trading volume (交易量, shares).';
COMMENT ON COLUMN stats.index_basic_stats.amount             IS 'Index trading amount (成交金额, 亿元).';
COMMENT ON COLUMN stats.index_basic_stats.change             IS 'Absolute price change from previous close.';
COMMENT ON COLUMN stats.index_basic_stats.change_pct         IS 'Percentage change from previous close (%).';
COMMENT ON COLUMN stats.index_basic_stats.has_intraday_5mins IS 'TRUE when 5-minute intraday bars exist for this (date, code) in stats.index_intraday_5min.';

-- ----------------------------------------------------------------------------
-- Table: index_valuation
--   ← PE ratio + constituent count (unique to indexes)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.index_valuation (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    pe                        NUMERIC(10,4),
    cons_number               NUMERIC(10,0),

    CONSTRAINT pk_index_valuation PRIMARY KEY (date, code),
    CONSTRAINT fk_index_valuation_date_code FOREIGN KEY (date, code) REFERENCES stats.index_identity(date, code)
);

COMMENT ON TABLE  stats.index_valuation                    IS 'Index valuation metrics: PE ratio + constituent count.';
COMMENT ON COLUMN stats.index_valuation.pe                IS 'Price-to-earnings ratio (PE).';
COMMENT ON COLUMN stats.index_valuation.cons_number       IS 'Number of constituent stocks in the index.';

-- ----------------------------------------------------------------------------
-- Table: index_tech_stats
--   ← Technical indicators (moving averages)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.index_tech_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    ma5                       NUMERIC(18,4),
    ma5_ratio                 NUMERIC(10,6),
    ma20                      NUMERIC(18,4),
    ma60                      NUMERIC(18,4),
    ma120                     NUMERIC(18,4),
    ma255                     NUMERIC(18,4),

    CONSTRAINT pk_index_tech_stats PRIMARY KEY (date, code),
    CONSTRAINT fk_index_tech_stats_date_code FOREIGN KEY (date, code) REFERENCES stats.index_identity(date, code)
);

COMMENT ON TABLE  stats.index_tech_stats                    IS 'Index technical indicators (moving averages).';
COMMENT ON COLUMN stats.index_tech_stats.ma5               IS '5-day moving average of close.';
COMMENT ON COLUMN stats.index_tech_stats.ma5_ratio         IS 'Close / MA5 - 1 (ratio of price to 5-day MA).';
COMMENT ON COLUMN stats.index_tech_stats.ma20              IS '20-day moving average of close.';
COMMENT ON COLUMN stats.index_tech_stats.ma60              IS '60-day moving average of close.';
COMMENT ON COLUMN stats.index_tech_stats.ma120             IS '120-day moving average of close.';
COMMENT ON COLUMN stats.index_tech_stats.ma255             IS '255-day moving average of close.';

-- ----------------------------------------------------------------------------
-- Table: index_intraday_5min
--   ← 5-minute intraday OHLCV bars (resampled from ~15s ticks)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.index_intraday_5min (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    time                      TIME          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    change                    NUMERIC(18,4),
    change_pct                NUMERIC(10,4),

    CONSTRAINT pk_index_intraday_5min PRIMARY KEY (date, code, time),
    CONSTRAINT fk_index_intraday_5min_date_code FOREIGN KEY (date, code) REFERENCES stats.index_identity(date, code)
);

COMMENT ON TABLE  stats.index_intraday_5min                    IS 'Index 5-minute intraday OHLCV bars.';
COMMENT ON COLUMN stats.index_intraday_5min.time               IS 'Bar end time (HH:MM:SS).';
COMMENT ON COLUMN stats.index_intraday_5min.open               IS 'Opening price of the 5-minute bar.';
COMMENT ON COLUMN stats.index_intraday_5min.high               IS 'Highest price during the 5-minute bar.';
COMMENT ON COLUMN stats.index_intraday_5min.low                IS 'Lowest price during the 5-minute bar.';
COMMENT ON COLUMN stats.index_intraday_5min.close              IS 'Closing price of the 5-minute bar.';
COMMENT ON COLUMN stats.index_intraday_5min.change             IS 'Absolute change from the bar''s open.';
COMMENT ON COLUMN stats.index_intraday_5min.change_pct         IS 'Percentage change from the bar''s open (%).';

-- Indexes
DROP INDEX IF EXISTS stats.idx_index_baseline_code_date;

CREATE INDEX IF NOT EXISTS idx_index_identity_code_date
    ON stats.index_identity (code, date DESC) INCLUDE (name);

CREATE INDEX IF NOT EXISTS idx_index_basic_stats_code_date
    ON stats.index_basic_stats (code, date);

CREATE INDEX IF NOT EXISTS idx_index_valuation_code_date
    ON stats.index_valuation (code, date);

CREATE INDEX IF NOT EXISTS idx_index_tech_stats_code_date
    ON stats.index_tech_stats (code, date);

CREATE INDEX IF NOT EXISTS idx_index_intraday_5min_code_date_time
    ON stats.index_intraday_5min (code, date, time);
