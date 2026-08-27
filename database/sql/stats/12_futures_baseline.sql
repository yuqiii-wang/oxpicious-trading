-- ============================================================================
--  Futures Baseline - Split Tables
--  Source: CFFEX archive CSV files (temps/cffex_archive/YYYYMM/YYYYMMDD_futures.csv)
--  Split into: futures_identity, futures_basic_stats
--  Reconstruct via: v_futures_baseline view (see 99_reconstruct_views.sql)
--
--  Schema mirrors the stock/index family for symmetry:
--    futures_identity      ~  stock_identity / index_identity
--    futures_basic_stats   ~  stock_basic_stats / index_basic_stats
--
--  CFFEX products:
--    Index futures (股指):  IC 中证500, IF 沪深300, IH 上证50, IM 中证1000
--    Bond futures  (国债):  T  10Y, TF 5Y, TL 30Y, TS 2Y
--
--  Contract code format: <PRODUCT><YYMM> e.g. IC2607 = CSI500 July-2026
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: futures_identity
--   Identity core (PK) for all futures sub-tables
--   Mirrors stock_identity (date, code) with product/bond/index classification
--   + underlying_code / underlying_name for cross-asset mapping
--     (index futures → underlying ETF/index code like 000300;
--      bond futures  → synthetic bond-code identifiers like T10)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.futures_identity (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    product_code              TEXT          NOT NULL,
    contract_month             TEXT          NOT NULL,
    contract_year_month       TEXT          NOT NULL,
    contract_type             TEXT          NOT NULL
        CHECK (contract_type IN ('index', 'bond')),
    name                      TEXT          NOT NULL,
    underlying_code           TEXT          NOT NULL,
    underlying_name           TEXT          NOT NULL,
    days_to_expiry            INTEGER       NOT NULL DEFAULT 0,

    CONSTRAINT pk_futures_identity PRIMARY KEY (code, date),
    CONSTRAINT chk_futures_identity_code_format
        CHECK (code ~ '^(IC|IF|IH|IM|T|TF|TL|TS)[0-9]{4}$')
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'futures_identity', 8);

COMMENT ON TABLE  stats.futures_identity              IS 'Futures identity: one row per (date, contract_code). PK shared by all futures sub-tables. Source: CFFEX archive CSV files (temps/cffex_archive/YYYYMM/YYYYMMDD_futures.csv).';
COMMENT ON COLUMN stats.futures_identity.code          IS 'Futures contract code, e.g. "IC2607" (CSI500 July 2026), "T2609" (10Y Treasury Sep 2026).';
COMMENT ON COLUMN stats.futures_identity.product_code   IS 'Product prefix extracted from contract code: IC/IF/IH/IM (index) or T/TF/TL/TS (bond).';
COMMENT ON COLUMN stats.futures_identity.contract_month IS 'YYMM portion of contract code, e.g. "2607" for July 2026.';
COMMENT ON COLUMN stats.futures_identity.contract_year_month IS 'Normalized "YYYY-MM" contract month, e.g. "2026-07".';
COMMENT ON COLUMN stats.futures_identity.contract_type IS 'index for stock index futures (IC/IF/IH/IM); bond for treasury bond futures (T/TF/TL/TS).';
COMMENT ON COLUMN stats.futures_identity.name           IS 'Chinese product name, e.g. "中证500股指期货", "10年期国债期货".';
COMMENT ON COLUMN stats.futures_identity.underlying_code IS 'Underlying asset code: index futures map to ETF/index codes (e.g. IF→000300 for CSI300); bond futures use synthetic codes (e.g. T→T10 for 10Y Treasury).';
COMMENT ON COLUMN stats.futures_identity.underlying_name IS 'Underlying asset name matching underlying_code (e.g. "沪深300" for 000300, "10年期国债" for T10).';
COMMENT ON COLUMN stats.futures_identity.days_to_expiry IS 'Calendar days from the trading date to the futures expiry date. Index futures (IC/IF/IH/IM) expire on the 3rd Friday; bond futures (T/TF/TL/TS) expire on the 2nd Friday of the contract month.';

-- ----------------------------------------------------------------------------
-- Table: futures_basic_stats
--   ← Daily OHLCV + settlement + volume + open interest
--   Mirrors stock_basic_stats / index_basic_stats
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.futures_basic_stats (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    settlement_price          NUMERIC(18,4),
    prev_settlement           NUMERIC(18,4),
    change                    NUMERIC(18,4),
    change_pct                NUMERIC(10,4),
    trading_shares            NUMERIC(24,4),
    trading_amount            NUMERIC(24,4),
    open_interest             NUMERIC(24,4),
    open_interest_change      NUMERIC(24,4),
    delta                     NUMERIC(18,8),

    CONSTRAINT pk_futures_basic_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_futures_basic_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.futures_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'futures_basic_stats', 8);

COMMENT ON TABLE  stats.futures_basic_stats               IS 'Futures daily OHLCV + settlement + volume + open interest. Source: CFFEX archive CSV columns mapped by builds/futures/loader.py.';
COMMENT ON COLUMN stats.futures_basic_stats.open          IS 'Opening price (今开盘).';
COMMENT ON COLUMN stats.futures_basic_stats.high          IS 'Highest price (最高价).';
COMMENT ON COLUMN stats.futures_basic_stats.low           IS 'Lowest price (最低价).';
COMMENT ON COLUMN stats.futures_basic_stats.close         IS 'Closing price (今收盘).';
COMMENT ON COLUMN stats.futures_basic_stats.settlement_price IS 'Daily settlement price (今结算).';
COMMENT ON COLUMN stats.futures_basic_stats.prev_settlement IS 'Previous settlement price (前结算). Used as the reference for change/change_pct.';
COMMENT ON COLUMN stats.futures_basic_stats.change        IS 'Change = close - previous settlement (涨跌1 = 今收盘 - 前结算).';
COMMENT ON COLUMN stats.futures_basic_stats.change_pct    IS 'Percentage change vs previous settlement (涨跌2 = % change).';
COMMENT ON COLUMN stats.futures_basic_stats.trading_shares IS 'Trading volume in contracts (成交量).';
COMMENT ON COLUMN stats.futures_basic_stats.trading_amount IS 'Trading turnover in yuan (成交金额).';
COMMENT ON COLUMN stats.futures_basic_stats.open_interest IS 'Open interest at end of day (持仓量, contracts).';
COMMENT ON COLUMN stats.futures_basic_stats.open_interest_change IS 'Change in open interest vs previous day (持仓变化).';
COMMENT ON COLUMN stats.futures_basic_stats.delta         IS 'Delta (always NULL for futures — CFFEX reports "--" which maps to NULL).';

-- ----------------------------------------------------------------------------
-- Migration: add underlying_code / underlying_name to existing databases
-- (safe to re-run — uses DO block to check column existence)
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'stats' AND table_name = 'futures_identity' AND column_name = 'underlying_code'
    ) THEN
        ALTER TABLE stats.futures_identity ADD COLUMN underlying_code TEXT NOT NULL DEFAULT '';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'stats' AND table_name = 'futures_identity' AND column_name = 'underlying_name'
    ) THEN
        ALTER TABLE stats.futures_identity ADD COLUMN underlying_name TEXT NOT NULL DEFAULT '';
    END IF;
END $$;

COMMENT ON COLUMN stats.futures_identity.underlying_code IS 'Underlying asset code: index futures map to ETF/index codes (e.g. IF→000300 for CSI300); bond futures use synthetic codes (e.g. T→T10 for 10Y Treasury).';
COMMENT ON COLUMN stats.futures_identity.underlying_name IS 'Underlying asset name matching underlying_code (e.g. "沪深300" for 000300, "10年期国债" for T10).';

-- ----------------------------------------------------------------------------
-- Migration: add days_to_expiry to existing databases
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'stats' AND table_name = 'futures_identity' AND column_name = 'days_to_expiry'
    ) THEN
        ALTER TABLE stats.futures_identity ADD COLUMN days_to_expiry INTEGER NOT NULL DEFAULT 0;
    END IF;
END $$;

COMMENT ON COLUMN stats.futures_identity.days_to_expiry IS 'Calendar days from the trading date to the futures expiry date. Index futures (IC/IF/IH/IM) expire on the 3rd Friday; bond futures (T/TF/TL/TS) expire on the 2nd Friday of the contract month.';

-- ----------------------------------------------------------------------------
-- Indexes for futures_identity
--   Legacy (code, date) index is redundant with the code-first PK — replaced
--   by a date-first index.
-- ----------------------------------------------------------------------------

DROP INDEX IF EXISTS stats.idx_futures_identity_code_date;

CREATE INDEX IF NOT EXISTS idx_futures_identity_date
    ON stats.futures_identity (date);

CREATE INDEX IF NOT EXISTS idx_futures_identity_product_date
    ON stats.futures_identity (product_code, date);

CREATE INDEX IF NOT EXISTS idx_futures_identity_type_date
    ON stats.futures_identity (contract_type, date);

CREATE INDEX IF NOT EXISTS idx_futures_identity_underlying_date
    ON stats.futures_identity (underlying_code, date);

-- ----------------------------------------------------------------------------
-- Indexes for futures_basic_stats
-- ----------------------------------------------------------------------------

DROP INDEX IF EXISTS stats.idx_futures_basic_stats_code_date;

CREATE INDEX IF NOT EXISTS idx_futures_basic_stats_date
    ON stats.futures_basic_stats (date);

CREATE INDEX IF NOT EXISTS idx_futures_basic_stats_settlement
    ON stats.futures_basic_stats (settlement_price);