
-- ----------------------------------------------------------------------------
--  Table: analysis.pe_pct_streaks  (pe band-break excursion streaks)
--    PK: (sec_type, code, date_year_month, period, pct_type,
--         start_date, end_date)
--
--  Episodes SHIFT with new data (the last streak stays open until a 6+-day
--  in-band gap or a side switch closes it; trailing in-band days may become
--  a bridged gap later), so the table is rebuilt WHOLESALE per sec_type (or
--  per code in single-code mode) on every run that processes the scope —
--  mov_ave_high_low_pct_streaks / mov_ave_market_hypes precedent. The side
--  (high / low leg) is NOT stored: the API derives it by comparing the
--  streak's end_value against the END month's band row (a streak never
--  switches sides, so the end day's own-month band decides exactly).
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Stretches when each security's PE broke out of its monthly band — under/over-valuation episodes with tolerated re-entries.
-- tags: pe, 市盈率, band break, 破区间, episode, 区间段, valuation, 估值

CREATE TABLE IF NOT EXISTS analysis.pe_pct_streaks (
    sec_type                        SMALLINT          NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code                            TEXT          NOT NULL,
    date_year_month                 DATE          NOT NULL,  -- the streak's START month FIRST day
    period                          INTEGER       NOT NULL,  -- 252, 504, 756, 1260
    pct_type                        INTEGER       NOT NULL,  -- 1 | 5 | 10 (percent)

    start_date                      DATE          NOT NULL,  -- first OUT-OF-BAND observation day
    end_date                        DATE          NOT NULL,  -- last OUT-OF-BAND observation day
    start_value                     NUMERIC(12,6) NOT NULL,  -- the pe value on start_date
    end_value                       NUMERIC(12,6) NOT NULL,  -- the pe value on end_date
    max_value                       NUMERIC(12,6) NOT NULL,  -- max pe over the span
    min_value                       NUMERIC(12,6) NOT NULL,  -- min pe over the span
    day_count                       INTEGER       NOT NULL,  -- trading rows in [start_date, end_date]
    std_dev                         NUMERIC(12,6) NOT NULL,  -- population std of day-over-day value changes

    CONSTRAINT pk_pe_pct_streaks PRIMARY KEY (sec_type, code, date_year_month, period, pct_type, start_date, end_date)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'pe_pct_streaks', 8);

CREATE INDEX IF NOT EXISTS idx_pe_pct_streaks_code
    ON analysis.pe_pct_streaks (sec_type, code, period, pct_type, start_date);

COMMENT ON TABLE  analysis.pe_pct_streaks            IS 'Band-BREAK excursion streaks of the pe series audited against analysis.pe_pct (2026-09 split of the former combined analysis.pe_and_dividend_pct_streaks — the metric column is gone; the table name carries the metric). A day is OUT-OF-BAND when its pe value is ABOVE its own month-band high_val or BELOW low_val. An excursion streak is the maximal consolidation of same-side out-of-band TRADING days where re-entries of up to 5 consecutive trading days are TOLERATED (bridged — in-band gap days stay inside the span and count in day_count); a longer in-band gap or a side switch ends the streak. start_date/end_date bound the span (first/last OUT-OF-BAND day). Non-trading vendor rows (ffilled weekday holidays) and NULL-pe rows are excluded before classification — spans, gap tolerance and day_count are in REAL observation days. Streaks can span calendar months (each day is tested against its OWN month''s band); date_year_month records the START month. Episodes shift with new data, so the table is rebuilt WHOLESALE per sec_type (per code in single-code mode) on every run that processes the scope. The side (high/low) is derived at query time from the END month''s band (a streak never switches sides). Internal step of analysis.pe_and_dividends (pct_streaks.py).';
COMMENT ON COLUMN analysis.pe_pct_streaks.sec_type   IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf, 1 = index, 2 = stock, 3 = industry (composite industry, code = industry_id).';
COMMENT ON COLUMN analysis.pe_pct_streaks.code       IS 'Security code (bare index code; ETF bare 6-digit; stock ticker with exchange suffix).';
COMMENT ON COLUMN analysis.pe_pct_streaks.date_year_month IS 'The streak''s START month, stored as the month''s FIRST day — the band-month context in which the excursion began. Streaks may span later months (each day is tested against its own month''s band). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.period     IS 'Lookback window of the audited band in observations: 252 / 504 / 756 / 1260 — the band''s period in analysis.pe_pct. Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.pct_type   IS 'Band tightness of the audited band in percent: 1 / 5 / 10 — the band''s pct_type in analysis.pe_pct. Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.start_date IS 'First observation day of the streak: the first OUT-OF-BAND day (pe above high_val or below low_val). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.end_date   IS 'Last observation day of the streak: the last OUT-OF-BAND day. Bridged in-band days (<= 5-day tolerated re-entries) lie strictly between start_date and end_date; trailing in-band days after end_date are NOT part of the streak (they may later become a bridged gap — hence the wholesale rebuild). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.start_value IS 'The pe value on start_date (the streak''s first out-of-band day).';
COMMENT ON COLUMN analysis.pe_pct_streaks.end_value  IS 'The pe value on end_date (the streak''s last out-of-band day) — compared against the END month''s band at query time to derive the side.';
COMMENT ON COLUMN analysis.pe_pct_streaks.max_value  IS 'Maximum pe over all observation days in the streak span [start_date, end_date] (including bridged in-band days) — for a high streak this is the stretch''s peak stretch vs the trailing history.';
COMMENT ON COLUMN analysis.pe_pct_streaks.min_value  IS 'Minimum pe over all observation days in the streak span [start_date, end_date] (including bridged in-band days).';
COMMENT ON COLUMN analysis.pe_pct_streaks.day_count  IS 'Number of trading rows in the streak span [start_date, end_date] — out-of-band days plus bridged in-band days (tolerated <= 5-day re-entries).';
COMMENT ON COLUMN analysis.pe_pct_streaks.std_dev    IS 'Population standard deviation (ddof=0) of day-over-day pe changes within the streak span, in the metric''s own units (0.00 for single-day streaks).';
