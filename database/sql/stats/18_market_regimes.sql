-- ============================================================================
--  Table: stats.market_regimes  (daily market-REGIME state registry)
--
--  REPLACES stats.mov_ave_market_hypes (the boolean episode detector —
--  dropped below; builds/market_hypes is superseded by
--  builds/market_regimes). One row per (sec_type, code, TRADING date) —
--  the DAILY regime label every downstream consumer splits by
--  (analysis_forecasts bucket axes, analysis_signals strategies /
--  history rows, live.live_signals, the data_viz regime shading).
--
--  REGIME TAXONOMY (4 exhaustive states; the 2026-09 market_regimes
--  study, docs/market_regimes_study.md + the Phase-A acceptance):
--    'hot'    vol_z >  vol_bar AND amt_z >  amt_bar  — elevated
--             volatility WITH volume expansion (the old "hyped")
--    'panic'  vol_z >  vol_bar AND amt_z <= amt_bar  — elevated
--             volatility WITHOUT volume expansion (exhaustion /
--             unwind; the old AND-gated boolean could NEVER flag it)
--    'quiet'  vol_z <= vol_bar AND amt_z >  amt_bar  — volume
--             expansion without price movement (accumulation /
--             distribution)
--    'calm'   everything else (the old "not hyped")
--
--  DETECTOR (all inputs TRAILING + SHIFTED 1 row — no look-ahead, the
--  px_vol log_level convention; the RETIRED market_hypes build used a
--  look-ahead CENTERED ±10y percentile base, inconsistent between
--  backtest and live):
--    vol leg  vol = sqrt(EWMA(ret_1d^2, span=vol_span, min_periods=
--             vol_min_periods)) per code; vol_z = (vol - mu)/sigma of
--             vol's own trailing vol_z_window-row moments (shift 1,
--             min vol_z_min_periods). RETURN-based — replaces the old
--             rolling sigma of the PRICE LEVEL, which conflated trend
--             with volatility and lagged turmoils (the reason the old
--             build needed a 30th-pct std bandaid).
--    amt leg  amt_z = (log(trading_amount) - mu)/sigma of the code's
--             own trailing amt_z_window-row log-amount moments (shift
--             1, min amt_z_min_periods) — the px_vol family's
--             log_level metric verbatim (one shared volume-state
--             definition across the repo).
--
--  NO-STATE days (the code's first vol_z_min_periods rows): a row IS
--  written with regime='calm' and NULL vol_z/amt_z — an explicit label
--  instead of a hidden consumer-side default (old semantics: young
--  codes were simply never hyped). Every (code, date) with a positive
--  trading_amount therefore has exactly one row.
--
--  The recorded build-parameter columns (vol_span / vol_min_periods /
--  vol_z_window / vol_z_min_periods / amt_z_window / amt_z_min_periods
--  / vol_bar / amt_bar) mirror the mov_ave_price_vs_amt registry's
--  parameter convention — consumers can audit the classification.
--
--  REBUILD SEMANTICS (margin_changes precedent — also the old
--  market_hypes convention): trailing-window stats never change past
--  rows, but ETF adj_close back-adjustments DO rewrite history, so
--  every run DELETEs the whole scope (one sec_type, or one code in
--  --code mode) and recomputes from the FULL per-code history.
--
--  Derived table: stats.market_regime_spans — one row per CONTIGUOUS
--  same-regime run over this table, MATERIALIZED by builds.market_regimes
--  (the same run collapses the daily states it just wrote). The UI
--  shading source that replaces the old episode rows.
--
--  POPULATION: python -m builds.market_regimes (ETF + Index + Stock
--  in one table, sec_type discriminates).
-- ============================================================================

-- stats.market_regime_spans used to be a VIEW (gaps-and-islands
-- recomputed on every query); it is a TABLE now. DROP VIEW and DROP
-- TABLE each reject the other's relkind even with IF EXISTS, so drop
-- the legacy view form conditionally before the unconditional table
-- drop — the file stays re-runnable from either prior state.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'stats'
          AND c.relname = 'market_regime_spans'
          AND c.relkind = 'v'
    ) THEN
        DROP VIEW stats.market_regime_spans;
    END IF;
END
$$;
DROP TABLE IF EXISTS stats.market_regime_spans CASCADE;
DROP TABLE IF EXISTS stats.market_regimes CASCADE;
-- RETIRED by this file (replaced by stats.market_regimes +
-- stats.market_regime_spans; builds.market_hypes -> builds.market_regimes).
DROP TABLE IF EXISTS stats.mov_ave_market_hypes CASCADE;

CREATE TABLE stats.market_regimes (
    sec_type        TEXT         NOT NULL,  -- 'etf' | 'index' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,
    regime          TEXT         NOT NULL,  -- 'calm' | 'hot' | 'panic' | 'quiet'

    -- evidence (NULL on no-state days — regime defaults to 'calm')
    vol             DOUBLE PRECISION,       -- sqrt(EWMA(ret_1d^2)) level
    vol_z           DOUBLE PRECISION,       -- z vs own trailing moments (shift 1)
    amt_z           DOUBLE PRECISION,       -- z of log(trading_amount), shift 1

    -- recorded build parameters (defaults = the study's Phase-A calibration)
    vol_span                 SMALLINT   NOT NULL DEFAULT 20,
    vol_min_periods          SMALLINT   NOT NULL DEFAULT 10,
    vol_z_window             SMALLINT   NOT NULL DEFAULT 255,
    vol_z_min_periods        SMALLINT   NOT NULL DEFAULT 60,
    amt_z_window             SMALLINT   NOT NULL DEFAULT 255,
    amt_z_min_periods        SMALLINT   NOT NULL DEFAULT 60,
    vol_bar                  NUMERIC(6,4) NOT NULL DEFAULT 1.0,
    amt_bar                  NUMERIC(6,4) NOT NULL DEFAULT 1.0,

    CONSTRAINT pk_market_regimes PRIMARY KEY (code, sec_type, date),
    CONSTRAINT ck_market_regimes_regime
        CHECK (regime IN ('calm', 'hot', 'panic', 'quiet'))
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'market_regimes', 8);

COMMENT ON TABLE  stats.market_regimes IS 'Daily market-REGIME state registry: one row per (sec_type, code, trading date) classifying the day into ONE of four exhaustive regimes — hot (vol_z > vol_bar AND amt_z > amt_bar: elevated volatility WITH volume expansion, the retired boolean "hyped"), panic (vol_z > vol_bar AND amt_z <= amt_bar: elevated volatility WITHOUT volume expansion — exhaustion/unwind, invisible to the old AND-gated hype boolean), quiet (vol_z <= vol_bar AND amt_z > amt_bar: volume expansion without price movement) or calm (everything else). vol_z = z of the code''s EWMA realized return-volatility (sqrt(EWMA(ret_1d^2, span=20))) vs its own trailing 255-row moments, amt_z = z of log(trading_amount) vs its own trailing 255-row moments — BOTH legs shifted 1 row (no look-ahead; the px_vol log_level convention — unlike the retired stats.mov_ave_market_hypes centered ±10y look-ahead percentile base). Days without state yet (first 60 rows) carry regime=''calm'' with NULL evidence. The parameter columns record the build''s calibration. Replaces stats.mov_ave_market_hypes (dropped); built by builds.market_regimes, wholesale-rebuilt per scope on every run (margin_changes
precedent). Derived shading table: stats.market_regime_spans,
materialized by the same build.';
COMMENT ON COLUMN stats.market_regimes.regime IS 'The day''s regime label (exhaustive): hot = vol_z > vol_bar AND amt_z > amt_bar; panic = vol_z > vol_bar AND amt_z <= amt_bar; quiet = vol_z <= vol_bar AND amt_z > amt_bar; calm = everything else. No-state days (NULL z''s) are labeled calm.';
COMMENT ON COLUMN stats.market_regimes.vol     IS 'Evidence: sqrt(EWMA(ret_1d^2, span=vol_span, min_periods=vol_min_periods)) — the EWMA realized volatility level the vol leg z-scores.';
COMMENT ON COLUMN stats.market_regimes.vol_z   IS 'Evidence: (vol - mu)/sigma of the code''s own trailing vol_z_window-row vol moments, SHIFTED 1 row (no look-ahead). NULL on no-state days.';
COMMENT ON COLUMN stats.market_regimes.amt_z   IS 'Evidence: (log(trading_amount) - mu)/sigma of the code''s own trailing amt_z_window-row log-amount moments, SHIFTED 1 row (no look-ahead — the px_vol log_level metric). NULL on no-state days.';
COMMENT ON COLUMN stats.market_regimes.vol_bar IS 'Recorded build parameter: the vol_z bar separating elevated from normal volatility (the hot/panic vs calm/quiet split). NOT part of the PK; changing it requires a wholesale rebuild.';
COMMENT ON COLUMN stats.market_regimes.amt_bar IS 'Recorded build parameter: the amt_z bar separating volume expansion from normal (the hot/quiet vs calm/panic split). NOT part of the PK; changing it requires a wholesale rebuild.';

-- ----------------------------------------------------------------------------
--  Derived TABLE: contiguous same-regime spans (the UI shading source —
--  replaces the retired mov_ave_market_hypes EPISODE rows). One row per
--  maximal run of consecutive trading dates holding the SAME regime:
--  (start_date, end_date) inclusive + span_days (trading-date count).
--  MATERIALIZED by builds.market_regimes — the same run computes the
--  spans from the daily states it just wrote and replaces the scope's
--  rows wholesale alongside the registry (--force truncates both).
--  Formerly a per-query VIEW whose gaps-and-islands recomputed on every
--  /api/analysis/market-regimes call; the table turns that endpoint
--  into a pure row read.
-- ----------------------------------------------------------------------------
CREATE TABLE stats.market_regime_spans (
    sec_type   TEXT    NOT NULL,  -- 'etf' | 'index' | 'stock'
    code       TEXT    NOT NULL,
    regime     TEXT    NOT NULL,  -- 'calm' | 'hot' | 'panic' | 'quiet'
    start_date DATE    NOT NULL,  -- inclusive
    end_date   DATE    NOT NULL,  -- inclusive
    span_days  INTEGER NOT NULL,  -- trading-date count in [start_date, end_date]

    CONSTRAINT pk_market_regime_spans
        PRIMARY KEY (code, sec_type, regime, start_date),
    CONSTRAINT ck_market_regime_spans_regime
        CHECK (regime IN ('calm', 'hot', 'panic', 'quiet')),
    CONSTRAINT ck_market_regime_spans_dates
        CHECK (start_date <= end_date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed on code — same convention as the
-- daily registry (the per-code endpoint read prunes to one partition).
SELECT public.create_hash_partitions('stats', 'market_regime_spans', 8);

COMMENT ON TABLE stats.market_regime_spans IS 'One row per CONTIGUOUS run of same-regime days over stats.market_regimes: (start_date, end_date) inclusive span + span_days (trading-date count). The UI regime-shading source replacing the retired mov_ave_market_hypes episode rows — hot spans shade as the old "hyped" bands did; panic/quiet get their own colors. MATERIALIZED by builds.market_regimes (wholesale-rebuilt per scope alongside the registry; formerly a per-query view).';
