-- ============================================================================
--  mov_ave_spread suite — split of the former 03_mov_ave_spreads.sql.
--  Shared conventions (sec_type handling, the NO-FK derived-data design,
--  rebuild semantics): see 01_mov_ave_spreads_detail.sql.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_high_low_pct_streaks  (band-BREAK excursion
--         streaks audited against analysis.mov_ave_high_low_pct bands,
--         one row per excursion streak x security x period x pct_type)
--
--  For each (period, pct_type) band, a day is OUT-OF-BAND when its
--  adjusted close falls ABOVE high_val or BELOW low_val (close-based
--  breakout test — intraday spikes don't trigger, the streak's own
--  high/low columns carry that information). An excursion STREAK is
--  the maximal consolidation of same-side out-of-band days where
--  brief re-entries into the band of up to HIGH_LOW_PCT_GAP_TOLERANCE
--  (5) consecutive trading days are TOLERATED (bridged — the 5-day
--  gap stays inside the streak's span); a longer in-band gap ends the
--  streak, as does a side switch (above -> below or vice versa starts
--  a new streak). start_date/end_date bound the streak's span (the
--  first/last OUT-OF-BAND trading row; bridged in-band days in
--  between count in day_count). Episodes can span calendar months;
--  date_year_month records the streak's START month (the band month
--  context in which the excursion began).
--
--  EPISODES SHIFT when new data arrives: the last streak of a code is
--  open-ended until a 6+-day in-band gap (or a side switch) closes it,
--  and trailing in-band days may later become a bridged gap — so, like
--  mov_ave_market_hypes episodes (margin_changes precedent), streaks
--  are rebuilt WHOLESALE per sec_type on every run that processes the
--  sec_type; per-date PK coverage diffing does not apply.
--
--  Populated by the internal streaks step of `analyze.mov_ave_spread`
--  (see high_low_pct_streaks.py; joins the parent source DataFrame's
--  open/high/low/price/trading_amount against the bands table — bands
--  are computed by the high-low-percentile step earlier in the same
--  run).
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis.mov_ave_high_low_pct_streaks CASCADE;
DELETE FROM analysis.analysis_identity WHERE name = 'mov_ave_high_low_pct_streaks';

CREATE TABLE analysis.mov_ave_high_low_pct_streaks (
    sec_type                        TEXT          NOT NULL,  -- 'etf' | 'index' | 'stock'
    code                            TEXT          NOT NULL,
    date_year_month                 DATE          NOT NULL,  -- the streak's START month FIRST day (band month context)
    period                          INTEGER       NOT NULL,  -- 255, 500, 750, 1275
    pct_type                        INTEGER       NOT NULL,  -- 1 | 5 | 10 (percent)

    start_date                      DATE          NOT NULL,  -- the first trading row in the streak
    end_date                        DATE          NOT NULL,  -- the last trading row in the streak
    open                            NUMERIC(10,2) NOT NULL,  -- the open price on start_date
    close                           NUMERIC(10,2) NOT NULL,  -- the close price on end_date
    high                            NUMERIC(10,2) NOT NULL,  -- the high price in the streak
    low                             NUMERIC(10,2) NOT NULL,  -- the low price in the streak
    day_count                       INTEGER       NOT NULL,  -- the number of trading rows in the streak
    std_dev                         NUMERIC(10,2) NOT NULL,  -- the standard deviation of daily price changes in the streak, rounded to 2 decimals
    daily_ave_trading_amt           NUMERIC(24,2) NOT NULL,  -- the average trading amount in the streak, rounded to 2 decimals

    CONSTRAINT pk_mov_ave_high_low_pct_streaks PRIMARY KEY (sec_type, code, date_year_month, period, pct_type, start_date, end_date)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'mov_ave_high_low_pct_streaks', 8);

COMMENT ON TABLE  analysis.mov_ave_high_low_pct_streaks            IS 'Band-BREAK excursion streaks audited against analysis.mov_ave_high_low_pct (ETF + Index + Stock). One row per excursion streak per (sec_type, code, period, pct_type): a day is OUT-OF-BAND when its adjusted close falls ABOVE the band''s high_val or BELOW low_val (close-based breakout test — intraday spikes do not trigger; the streak''s own high/low columns carry that information). An excursion streak is the maximal consolidation of same-side out-of-band days where re-entries into the band of up to 5 consecutive trading days are TOLERATED (bridged — the in-band gap stays inside the streak''s span); a longer in-band gap ends the streak, as does a side switch (above -> below or vice versa starts a new streak). start_date/end_date bound the span (first/last OUT-OF-BAND trading row; bridged in-band days in between count in day_count). Streaks can span calendar months; date_year_month records the START month. Episodes SHIFT with new data (the last streak of a code is open-ended until a 6+-day in-band gap or side switch closes it, and trailing in-band days may become a bridged gap later), so streaks are rebuilt WHOLESALE per sec_type on every run that processes the sec_type (mov_ave_market_hypes episodes / margin_changes precedent). Internal step of analyze.mov_ave_spread (high_low_pct_streaks.py), joining the parent source DataFrame against the bands table computed earlier in the same run.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.sec_type   IS 'Security type: etf (ETF), index (CSI-style index), or stock (individual equity).';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.code       IS 'Ticker. ETFs use exchange suffix (e.g. "510050.SS"); indices use bare code (e.g. "000300").';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.date_year_month IS 'The streak''s START month, stored as the month''s FIRST day — the band-month context in which the excursion began (the first out-of-band day is tested against this month''s band row). Streaks may span later months; each day is tested against its OWN month''s band. Part of the PK.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.period     IS 'Lookback window length of the audited band in trading rows: 255 / 500 / 750 / 1275 (~1 / 2 / 3 / 5 trading years) — the band''s `period` in analysis.mov_ave_high_low_pct. Part of the PK (streaks are audited per band period).';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.pct_type   IS 'Band tightness parameter of the audited band in percent: 1 / 5 / 10 — the band''s `pct_type` in analysis.mov_ave_high_low_pct (the band whose high_val/low_val the close breaks). Part of the PK (streaks are audited per band level).';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.start_date IS 'First trading row of the streak: the first OUT-OF-BAND day (adjusted close above high_val or below low_val). Part of the PK.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.end_date   IS 'Last trading row of the streak: the last OUT-OF-BAND day. Bridged in-band days (<= 5-day tolerated re-entries) lie strictly between start_date and end_date; trailing in-band days after end_date are NOT part of the streak (they may later become a bridged gap — episodes shift, hence the wholesale rebuild). Part of the PK.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.open       IS 'Adjusted open price on start_date (the streak''s first out-of-band day), rounded to 2 decimals.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.close      IS 'Adjusted close price on end_date (the streak''s last out-of-band day), rounded to 2 decimals.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.high       IS 'Maximum adjusted HIGH price over all trading rows in the streak span [start_date, end_date] (including bridged in-band days) — may exceed the band''s high_val, rounded to 2 decimals.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.low        IS 'Minimum adjusted LOW price over all trading rows in the streak span [start_date, end_date] (including bridged in-band days) — may fall below the band''s low_val, rounded to 2 decimals.';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.day_count  IS 'Number of trading rows in the streak span [start_date, end_date] — out-of-band days plus bridged in-band days (tolerated <= 5-day re-entries).';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.std_dev    IS 'Population standard deviation (ddof=0) of day-over-day adjusted-close changes within the streak span, in PRICE units, rounded to 2 decimals (0.00 for single-day streaks).';
COMMENT ON COLUMN analysis.mov_ave_high_low_pct_streaks.daily_ave_trading_amt IS 'Average trading_amount over all trading rows in the streak span (including bridged in-band days; NULL-amount rows are skipped), rounded to 2 decimals. 0.00 is the sentinel for spans with NO trading-amount data at all (some indices publish NULL amounts on scattered dates).';
