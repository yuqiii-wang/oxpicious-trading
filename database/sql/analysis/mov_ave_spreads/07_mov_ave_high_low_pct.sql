-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_high_low_pct  (multi-period high/low price
--         percentile BAND, one row per security x calendar month x
--         period x pct_type)
--
--  Symmetric quantile ENVELOPEs of where a security's daily prices
--  traded over its recent history: low_val is the pct_type-th
--  percentile of daily LOW prices and high_val the (100 - pct_type)-th
--  percentile of daily HIGH prices over the trailing `period`-row
--  window ENDING at the month's last trading row (255/500/750/1275
--  trading rows = ~1/2/3/5 trading years — the ma255 yearly-window
--  precedent). pct_type 1 -> near-full range of the window ([1st pct
--  of lows, 99th pct of highs]); pct_type 10 -> core envelope ([10th,
--  90th]). One band per calendar month per (sec_type, code) per
--  (period, pct_type) — 12 bands per month.
--
--  Populated by the internal high-low-percentile step of
--  `analyze.mov_ave_spread` (see high_low_pct.py; reuses the parent
--  source DataFrame's high / low columns — no second DB round-trip).
--  Trailing window: historical bands are IMMUTABLE once computed (new
--  dates never change a past month's trailing percentile), so only
--  missing (code, month) pairs are (re)computed — a pair is complete
--  when all 12 (period, pct_type) rows exist; --force rebuilds the
--  whole scope.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis.mov_ave_high_low_pct CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_high_low_pct';

CREATE TABLE analysis.mov_ave_high_low_pct (
    sec_type                        TEXT          NOT NULL,  -- 'etf' | 'index' | 'stock'
    code                            TEXT          NOT NULL,
    date_year_month                 DATE          NOT NULL,  -- the month's FIRST day, lookback period
    period                          INTEGER       NOT NULL,  -- 255, 500, 750, 1275
    pct_type                        INTEGER       NOT NULL,  -- 1 | 5 | 10 (percent)
    high_val                        NUMERIC(10,2) NOT NULL,
    low_val                         NUMERIC(10,2) NOT NULL,

    CONSTRAINT pk_mov_ave_high_low_pct PRIMARY KEY (sec_type, code, date_year_month, period, pct_type)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'mov_ave_high_low_pct', 8);

COMMENT ON TABLE  analysis.mov_ave_high_low_pct            IS 'Multi-period high/low price percentile BAND (ETF + Index + Stock). One row per (sec_type, code, date_year_month, period, pct_type): a symmetric quantile envelope of the security''s daily price range over the lookback period — low_val = the pct_type-th percentile of daily LOW prices, high_val = the (100 - pct_type)-th percentile of daily HIGH prices (linear interpolation), both over the TRAILING (backward-only) window of `period` trading rows (255/500/750/1275 = ~1/2/3/5 trading years — the ma255 yearly-window precedent) ENDING at date_year_month''s last trading row. pct_type 1 = near-full range of the window ([1st pct of lows, 99th pct of highs]); pct_type 10 = core envelope ([10th, 90th]). One band per calendar month, anchored at the month''s last trading row and stored under the month''s first day — 12 bands (4 periods x 3 pct_types) per (sec_type, code, month). Windows near a code''s history start are naturally truncated; fewer than 255 rows (1 trading year) of history yields no band. Trailing windows make historical bands immutable once computed — only missing (code, month) pairs are computed incrementally (a pair is complete when all 12 rows exist); --force rebuilds the whole scope. Internal step of analyze.mov_ave_spread (high_low_pct.py), reusing the parent source DataFrame''s high/low columns.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct.sec_type   IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct.code       IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct.date_year_month IS 'Calendar month of the band, stored as the month''s FIRST day (e.g. 2026-09-01) — the anchor of the lookback period: the percentile window ENDS (inclusive) at the month''s LAST trading row and spans the preceding `period` trading rows. Part of the PK.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct.period     IS 'Lookback window length in trading rows: 255 / 500 / 750 / 1275 (~1 / 2 / 3 / 5 trading years — the ma255 yearly-window precedent). The window is TRAILING — it ends inclusive at date_year_month''s last trading row and reaches back `period` rows. Part of the PK (one band set per period); near a code''s history start the window is naturally truncated to the available rows (bands appear once >= 255 rows of history exist).';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct.pct_type   IS 'Band tightness parameter in percent: the LOW leg uses the pct_type-th percentile of daily low prices, the HIGH leg the (100 - pct_type)-th percentile of daily high prices. Allowed values 1 / 5 / 10 — 1 = near-full range of the window, 5 = wide envelope, 10 = core envelope. Part of the PK (one band set per level).';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct.high_val   IS 'Upper band: the (100 - pct_type)-th percentile (linear interpolation) of daily HIGH prices over the trailing `period`-row window ending at date_year_month''s last trading row. Price units, rounded to 2 decimals.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct.low_val    IS 'Lower band: the pct_type-th percentile (linear interpolation) of daily LOW prices over the same trailing `period`-row window. Price units, rounded to 2 decimals; <= high_val for any real price history.';
