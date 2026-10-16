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
    exchange                  TEXT,
    name                      TEXT          NOT NULL,

    CONSTRAINT pk_etf_identity PRIMARY KEY (code, date),
    CONSTRAINT chk_etf_identity_code_format
        CHECK (code ~ '^\d{6}$')
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_identity', 8);

-- Idempotent migration: replace the legacy code_suffix column with the
-- canonical exchange column (mirroring the canonical source-CSV schema).
-- Suffix-guarded: bare codes (post-2026-09-30 convention, OTC feeders)
-- never enter — split_part on them would write '' into exchange.
UPDATE stats.etf_identity
   SET exchange = split_part(code, '.', 2)
 WHERE (exchange IS NULL OR exchange = '')
   AND code ~ '^\d{6}\.';

COMMENT ON TABLE  stats.etf_identity                 IS 'ETF identity: one row per (date, etf_code). PK (code, date) shared by all ETF sub-tables. Native HASH partitioned by code.';
COMMENT ON COLUMN stats.etf_identity.code           IS 'ETF/fund code, BARE 6-digit (e.g. "159007" SZSE, "510050" SSE) — the exchange suffix was removed 2026-09-30 (stocks keep theirs). OTC ETF feeder funds (联接基金, stats.sec_etf_links.feeder_code, loaded by builds.etf.feeder_nav) live here bare as well.';
COMMENT ON COLUMN stats.etf_identity.exchange       IS 'Listing venue: "SS" or "SZ" for exchange-listed ETFs, carried from the canonical source-CSV exchange column; NULL for OTC feeder funds (no listing). Authoritative listed-vs-OTC discriminator — never derive it from the code (the code is bare).';

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
    pe                        NUMERIC(18,4),
    eps                       NUMERIC(18,6),
    is_ohl_estimated          BOOLEAN       NOT NULL DEFAULT FALSE,
    has_intraday_5mins        BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_etf_basic_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_basic_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_basic_stats', 8);

COMMENT ON TABLE  stats.etf_basic_stats                    IS 'ETF raw basic_stats (yuan) + pe (harmonic-weighted constituent PE).';
COMMENT ON COLUMN stats.etf_basic_stats.is_ohl_estimated  IS 'TRUE when open/high/low were synthesized (not from source CSV) because the source row carries close-only data. Synthesis: open = previous close of the same code (NULL when no predecessor exists), high = low = close. FALSE when any real OHLC component came from the source.';
COMMENT ON COLUMN stats.etf_basic_stats.has_intraday_5mins IS 'TRUE when 5-minute intraday bars exist for this (date, code) (reserved for future ETF intraday support).';
COMMENT ON COLUMN stats.etf_basic_stats.pe                 IS 'Price-to-earnings ratio (PE). Computed by builds.etf via HARMONIC weighting of constituent stock PE from stats.stock_basic_stats by the LATEST stats.sec_composition snapshot (source_type=etf, temporal extrapolation): PE_etf = SUM(w_i) / SUM(w_i / PE_i). Loss-making constituents (NULL PE) excluded from both numerator and denominator. NULL when no composition or no constituent has positive PE.';
COMMENT ON COLUMN stats.etf_basic_stats.eps                IS 'Implied earnings per share (EPS), in yuan per single share, derived from the identity PE = price / EPS as eps = close / pe. NULL when pe is NULL (no composition or all constituents loss-making) or close is NULL. Populated by builds/etf/__main__.py at insert time (recomputed when the harmonic PE is backfilled).';

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
    ema6                      NUMERIC(18,4),
    ema10                     NUMERIC(18,4),
    ema20                     NUMERIC(18,4),
    ema60                     NUMERIC(18,4),
    ema120                    NUMERIC(18,4),
    ema255                    NUMERIC(18,4),
    trading_amt_per_pct_change NUMERIC(18,6),

    CONSTRAINT pk_etf_tech_stats PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_tech_stats_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_tech_stats', 8);

COMMENT ON TABLE  stats.etf_tech_stats                    IS 'ETF technical indicators (moving averages + EMAs).';
COMMENT ON COLUMN stats.etf_tech_stats.ma5               IS '5-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma5_ratio         IS 'Close / MA5 - 1 (ratio of price to 5-day MA).';
COMMENT ON COLUMN stats.etf_tech_stats.ma20              IS '20-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma60              IS '60-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma120             IS '120-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ma255             IS '255-day moving average of adj_close.';
COMMENT ON COLUMN stats.etf_tech_stats.ema6              IS '6-day exponential moving average of adj_close (span=6, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema10             IS '10-day exponential moving average of adj_close (span=10, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema20             IS '20-day exponential moving average of adj_close (span=20, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema60             IS '60-day exponential moving average of adj_close (span=60, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema120            IS '120-day exponential moving average of adj_close (span=120, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.ema255            IS '255-day exponential moving average of adj_close (span=255, adjust=False).';
COMMENT ON COLUMN stats.etf_tech_stats.trading_amt_per_pct_change IS 'Intraday net-move liquidity ratio: trading_amount / (adj_close - adj_open), SIGNED — positive on up days, negative on down days (the sign carries the move direction). trading_amount from stats.etf_liquidity_margin (yuan); the move is measured in ADJUSTED space (adj_open/adj_close from stats.etf_adjustment) because the raw close-open carries the split / ex-dividend gap on corp-action days. Zero move (adj_close = adj_open — flat / limit-locked day): denominator floored to 1.0, so the stored value equals the raw trading amount (mov_ave_spread zero-denominator convention — a pragmatic floor, NOT a true ratio). NULL when any input is NULL or |value| >= 1e12 (NUMERIC(18,6) bound). Reciprocal-Amihud liquidity gauge: HIGH = deep book (much capital absorbed per unit of move), LOW = thin market (little capital moved the price a lot). Computed by builds.etf (pipeline/features.py).';

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

    CONSTRAINT pk_etf_adjustment PRIMARY KEY (code, date),
    CONSTRAINT fk_etf_adjustment_date_code FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'etf_adjustment', 8);

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

-- ----------------------------------------------------------------------------
-- Table: etf_intraday_5min
--   ← 5-minute intraday OHLCV bars streamed from the SSE fund tab
--   (https://www.sse.com.cn/market/price/report/ "刷新" button JSONP source,
--    /exchange/fund endpoint). Mirrors stock_intraday_5min: the SSE list
--    endpoint publishes today's CUMULATIVE volume, so per-bar volume is
--    derived by subtracting the previous bar's cumulative volume from the
--    current bar's.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.etf_intraday_5min (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    exchange                  TEXT,
    time                      TIME          NOT NULL,
    open                      NUMERIC(18,4),
    high                      NUMERIC(18,4),
    low                       NUMERIC(18,4),
    close                     NUMERIC(18,4),
    trading_shares            NUMERIC(24,4),
    change                    NUMERIC(18,4),
    change_pct                NUMERIC(10,4),

    CONSTRAINT pk_etf_intraday_5min PRIMARY KEY (code, date, time)
) PARTITION BY HASH (code);

-- Idempotent migration: no FK to etf_identity on the streamer-owned bar
-- table. The streamer upserts identity rows before bars itself, and an FK
-- here made TRUNCATE stats.etf_identity CASCADE (builds.etf --force) wipe
-- the WHOLE bar history as collateral — streamer-owned data no build
-- restores. Same migration applied to index/stock/options intraday tables.
ALTER TABLE stats.etf_intraday_5min
    DROP CONSTRAINT IF EXISTS fk_etf_intraday_5min_date_code;

-- Idempotent migration: replace the legacy code_suffix column with exchange.
-- Suffix-guarded so bare codes never write '' into exchange.
UPDATE stats.etf_intraday_5min
   SET exchange = split_part(code, '.', 2)
 WHERE (exchange IS NULL OR exchange = '')
   AND code ~ '^\d{6}\.';
COMMENT ON COLUMN stats.etf_intraday_5min.exchange IS 'Listing venue: "SZ" or "SS", set by the streaming loaders; NULL should not occur (streamer covers listed funds only).';

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('stats', 'etf_intraday_5min', 16);

COMMENT ON TABLE  stats.etf_intraday_5min              IS 'ETF 5-minute intraday OHLCV bars streamed from the SSE fund tab (https://www.sse.com.cn/market/price/report/). Mirrors stock_intraday_5min.';
COMMENT ON COLUMN stats.etf_intraday_5min.time         IS 'Bar end time (HH:MM:SS); timestamp of the last 1-minute sample in the bar, truncated to the minute.';
COMMENT ON COLUMN stats.etf_intraday_5min.open         IS 'Opening price of the 5-minute bar (first sample latest price).';
COMMENT ON COLUMN stats.etf_intraday_5min.high         IS 'Highest latest price during the 5-minute bar.';
COMMENT ON COLUMN stats.etf_intraday_5min.low          IS 'Lowest latest price during the 5-minute bar.';
COMMENT ON COLUMN stats.etf_intraday_5min.close        IS 'Closing price of the 5-minute bar (last sample latest price).';
COMMENT ON COLUMN stats.etf_intraday_5min.trading_shares       IS 'Volume traded during the 5-minute window in shares (cumulative day volume at bar end minus cumulative volume at previous bar end).';
COMMENT ON COLUMN stats.etf_intraday_5min.change       IS 'Absolute change from the bar''s open (close - open).';
COMMENT ON COLUMN stats.etf_intraday_5min.change_pct   IS 'Percentage change from the bar''s open (%) = (close - open) / open * 100.';

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

-- The legacy (code, date) secondary indexes are superseded by the
-- code-first PK; the date-first indexes below keep cross-code scans cheap.

CREATE INDEX IF NOT EXISTS idx_etf_basic_stats_date
    ON stats.etf_basic_stats (date);
CREATE INDEX IF NOT EXISTS idx_etf_tech_stats_date
    ON stats.etf_tech_stats (date);
CREATE INDEX IF NOT EXISTS idx_etf_adjustment_date
    ON stats.etf_adjustment (date);
CREATE INDEX IF NOT EXISTS idx_etf_liquidity_margin_date
    ON stats.etf_liquidity_margin (date);

CREATE INDEX IF NOT EXISTS idx_etf_margin_split_events
    ON stats.etf_adjustment (date)
    WHERE is_split_event_day = 1;

-- (c) etf_intraday_5min — PK (code, date, time) serves the per-code
--     lookups; a date-first index restores cross-code intraday scans.

CREATE INDEX IF NOT EXISTS idx_etf_intraday_5min_date
    ON stats.etf_intraday_5min (date);

-- ----------------------------------------------------------------------------
-- Idempotent migration (2026-09-30): strip the exchange suffix from ETF
-- codes — ETF/fund codes are BARE 6-digit everywhere now (stocks keep
-- their .SS/.SZ/.BJ/.HK suffix). Listed-vs-OTC is discriminated by the
-- exchange column (SS/SZ = listed, NULL = OTC feeder), never by the code.
--
-- Atomic DO block (single statement → single transaction): collision
-- pre-check → drop the 4 identity FKs → strip code on all 6 tables
-- (hash-partition row movement: expect a one-time WAL burst; see
-- REPLICA_SYNC.md) → re-add FKs (plain ADD CONSTRAINT — NOT VALID is
-- unsupported on partitioned tables in PG17; validation of the ~1M-row
-- tables completes in seconds inside the same transaction).
-- Re-running is a no-op: the strip is guarded on suffix presence.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    def        text;
    n_suffixed bigint;
    n_collide  bigint;
BEGIN
    -- A stripped code must not already exist as a bare row (prefix
    -- families are disjoint by design — this is a belt-and-braces guard).
    SELECT count(*) INTO n_collide
      FROM stats.etf_identity i
     WHERE i.code ~ '^\d{6}\.'
       AND EXISTS (SELECT 1 FROM stats.etf_identity b
                    WHERE b.code = split_part(i.code, '.', 1));
    IF n_collide > 0 THEN
        RAISE EXCEPTION
            'stats.etf_identity: % suffixed codes would collide with existing bare codes',
            n_collide;
    END IF;

    SELECT count(*) INTO n_suffixed
      FROM stats.etf_identity WHERE code ~ '^\d{6}\.';
    IF n_suffixed > 0 THEN
        ALTER TABLE stats.etf_basic_stats      DROP CONSTRAINT fk_etf_basic_stats_date_code;
        ALTER TABLE stats.etf_tech_stats       DROP CONSTRAINT fk_etf_tech_stats_date_code;
        ALTER TABLE stats.etf_adjustment       DROP CONSTRAINT fk_etf_adjustment_date_code;
        ALTER TABLE stats.etf_liquidity_margin DROP CONSTRAINT fk_etf_liquidity_margin_date_code;

        UPDATE stats.etf_identity         SET code = split_part(code, '.', 1) WHERE code ~ '^\d{6}\.';
        UPDATE stats.etf_basic_stats      SET code = split_part(code, '.', 1) WHERE code ~ '^\d{6}\.';
        UPDATE stats.etf_tech_stats       SET code = split_part(code, '.', 1) WHERE code ~ '^\d{6}\.';
        UPDATE stats.etf_adjustment       SET code = split_part(code, '.', 1) WHERE code ~ '^\d{6}\.';
        UPDATE stats.etf_liquidity_margin SET code = split_part(code, '.', 1) WHERE code ~ '^\d{6}\.';
        UPDATE stats.etf_intraday_5min    SET code = split_part(code, '.', 1) WHERE code ~ '^\d{6}\.';

        ALTER TABLE stats.etf_basic_stats
            ADD CONSTRAINT fk_etf_basic_stats_date_code
            FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date);
        ALTER TABLE stats.etf_tech_stats
            ADD CONSTRAINT fk_etf_tech_stats_date_code
            FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date);
        ALTER TABLE stats.etf_adjustment
            ADD CONSTRAINT fk_etf_adjustment_date_code
            FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date);
        ALTER TABLE stats.etf_liquidity_margin
            ADD CONSTRAINT fk_etf_liquidity_margin_date_code
            FOREIGN KEY (code, date) REFERENCES stats.etf_identity(code, date);
    END IF;

    -- Bare-only CHECK from any starting state (strict-suffixed, optional-
    -- suffix relaxed, or already bare). Catches any missed suffixed writer.
    -- (Probe is backslash-free: LIKE '\d' is an escaped 'd', not a literal
    -- backslash-d — '{6}$' only occurs in the bare-only definition.)
    SELECT pg_get_constraintdef(oid) INTO def
      FROM pg_constraint
     WHERE conname = 'chk_etf_identity_code_format'
       AND conrelid = 'stats.etf_identity'::regclass
     LIMIT 1;
    IF def IS NULL OR def NOT LIKE '%{6}$%' THEN
        ALTER TABLE stats.etf_identity
            DROP CONSTRAINT IF EXISTS chk_etf_identity_code_format;
        ALTER TABLE stats.etf_identity
            ADD CONSTRAINT chk_etf_identity_code_format
            CHECK (code ~ '^\d{6}$');
    END IF;
END $$;
