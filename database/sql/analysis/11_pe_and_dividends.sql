-- ============================================================================
--  PE & Dividend Yield — per-(sec_type, code, date) valuation analytics,
--  SPLIT into one table per metric (2026-09; replaces the former combined
--  analysis.pe_and_dividends table):
--
--    Table: analysis.pe          — RAW PE per (sec_type, code, date)
--    Table: analysis.dividends   — trailing-12m dividend yield per
--                                  (sec_type, code, date)
--
--  Close price is NOT stored here — it lives in the stats schema
--  (stats.index_basic_stats.close, stats.etf_basic_stats.close,
--  stats.stock_basic_stats.close). Both tables mirror
--  analysis.mov_ave_spreads_detail's all-sec_types shape so they join 1:1
--  on (sec_type, code, date).
--
--    sec_type ∈ ('index' | 'etf' | 'stock')
--
--  ============================================================================
--  Table: analysis.pe
--    PK: (sec_type, code, date)
--
--  COLUMNS
--    pe — RAW PE (no smoothing): index from stats.index_valuation.pe; etf
--         from stats.etf_basic_stats.pe (pre-computed by builds.etf via
--         harmonic-weighted constituent PE); stock from
--         stats.stock_basic_stats.pe (pre-computed by builds.stock). NULL
--         when the source PE is <= 0 / NULL (no earnings / invalid data —
--         the invalid-value rule of compute.pe.clean_pe). A row exists
--         only where the source day exists (close IS NOT NULL); rows with
--         an invalid PE are NOT stored at all.
--
--  ============================================================================
--  Table: analysis.dividends
--    PK: (sec_type, code, date)
--
--  COLUMNS
--    dividend_yield — Trailing-12m dividend yield = (sum of per-share
--                     dividends going ex in the trailing 365-day window
--                     ending on `date`) / close. Stored as a FRACTIONAL
--                     ratio (0.035 = 3.5%), matching the convention used
--                     by analysis.mov_ave_spreads_detail gap columns and
--                     stats.index_tech_stats.ma5_ratio. Rows exist only
--                     where the yield is defined (strictly positive) —
--                     non-payers have NO rows (exact 0.0 is never
--                     produced).
--
--                     Source per sec_type:
--                       stock -> SUM(stats.stock_dividends.
--                                    dividend_per_share_pre_tax) WHERE
--                                    code = stock_code AND ex_dividend_date
--                                    ∈ (date - 365d, date]. Pre-tax is used
--                                    for cross-issue comparability (tax
--                                    treatment varies by holding period).
--                       etf   -> SUM(stats.etf_adjustment.
--                                    implied_dividend_per_share) over the
--                                    trailing-12m ex-dividend events for
--                                    the ETF. implied_dividend_per_share is
--                                    the per-event increment (vs the running
--                                    cum_dividend_per_share), so the SUM
--                                    over the window = trailing-12m DPS.
--                       index -> Aggregated from CONSTITUENT STOCK dividends
--                                weighted by composition — the CAP-WEIGHTED
--                                AVERAGE of the constituents' own trailing-12m
--                                YIELDS:
--                                  dividend_yield_t = SUM_s ( w_s × dps_s_t / close_s_t )
--                                where s ranges over the index's constituent
--                                stocks from the LATEST stats.sec_composition
--                                snapshot (source_type='index',
--                                temporal-extrapolation — same snapshot used
--                                for all dates, mirroring industry_sentiments),
--                                w_s = weight_pct_s / 100 (0..1), dps_s_t =
--                                trailing-12m DPS of stock s, and close_s_t =
--                                stock s's close on the date (both per-share).
--                                UNIT NOTE: the former formula (weighted DPS /
--                                index close) divided per-share CNY by index
--                                POINTS — capitalization weights do not
--                                convert between those units, so it
--                                understated the yield by ~100x (the SSE
--                                composite showed ~0.05% instead of ~2.5%).
--
--  POPULATION
--    analysis.pe_and_dividends (Python module; one pipeline computes both
--    metrics — they share the fetch — and writes the two tables). Per
--    project rule, ALL INSERTs are in Python — no raw INSERT...SELECT SQL
--    in this file (the one-time split migration below is the only
--    exception, mirroring the schema-migration precedent of
--    analysis_forecasts/02_mov_rsi_mov_std.sql).
--
--  Register in analysis.analysis_identity (name='pe' and name='dividends').
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.pe  (daily raw PE)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.pe (
    sec_type        SMALLINT         NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- Raw PE (all sec_types — see the source table per sec_type above).
    -- Strictly positive or NULL... never stored: rows with an invalid
    -- (<= 0 / NULL) source PE are NOT inserted at all.
    pe              NUMERIC(10,4) NOT NULL,

    CONSTRAINT pk_pe PRIMARY KEY (code, sec_type, date),
    CONSTRAINT chk_pe_sec_type
        CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'pe', 16);

CREATE INDEX IF NOT EXISTS idx_pe_date
    ON analysis.pe (date);
CREATE INDEX IF NOT EXISTS idx_pe_sec_type_date
    ON analysis.pe (sec_type, date);

COMMENT ON TABLE  analysis.pe        IS 'Per-(code, sec_type, date) RAW price-to-earnings ratio (no smoothing; 2026-09 split from the former combined analysis.pe_and_dividends). Sources per sec_type: index = stats.index_valuation.pe; etf = stats.etf_basic_stats.pe (pre-computed by builds.etf via harmonic-weighted constituent PE); stock = stats.stock_basic_stats.pe (pre-computed by builds.stock). Rows exist only for source days with a valid (strictly positive) PE — invalid (<= 0 / NULL) PE days have NO row. Built by analysis.pe_and_dividends; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.pe.sec_type IS 'Security type as its stats.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.pe.code     IS 'Security code (bare index code e.g. 000300; ETF bare e.g. 159001; stock ticker with exchange suffix e.g. 600008.SS).';
COMMENT ON COLUMN analysis.pe.date     IS 'Trading date.';
COMMENT ON COLUMN analysis.pe.pe       IS 'RAW price-to-earnings ratio, no smoothing (2026-09: replaced the legacy pe_ma20 20-day MA, then the combined table split per metric). Sources per sec_type: index = stats.index_valuation.pe; etf = stats.etf_basic_stats.pe (pre-computed by builds.etf via harmonic-weighted constituent PE); stock = stats.stock_basic_stats.pe (pre-computed by builds.stock).';

-- ----------------------------------------------------------------------------
--  Table: analysis.dividends  (daily trailing-12m dividend yield)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.dividends (
    sec_type        SMALLINT         NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- Trailing-12m dividend yield (D/P) as a FRACTIONAL ratio
    -- (0.035 = 3.5%). Strictly positive — non-payers have NO row.
    dividend_yield  NUMERIC(10,6) NOT NULL,

    CONSTRAINT pk_dividends PRIMARY KEY (code, sec_type, date),
    CONSTRAINT chk_dividends_sec_type
        CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'dividends', 16);

CREATE INDEX IF NOT EXISTS idx_dividends_date
    ON analysis.dividends (date);
CREATE INDEX IF NOT EXISTS idx_dividends_sec_type_date
    ON analysis.dividends (sec_type, date);

COMMENT ON TABLE  analysis.dividends        IS 'Per-(code, sec_type, date) trailing-12m dividend yield (D/P, fractional ratio; 2026-09 split from the former combined analysis.pe_and_dividends). Close is read live from stats at compute time (NOT stored here). stock: SUM(stock_dividends.dividend_per_share_pre_tax over trailing 365d)/close; etf: SUM(etf_adjustment.implied_dividend_per_share over trailing 365d)/close; index: cap-weighted average of constituent trailing-12m yields (SUM weight_fraction x dps/close, per-share numerator and denominator) using LATEST sec_composition snapshot (source_type=''index'', temporal extrapolation). Rows exist only where the yield is defined (strictly positive) — non-payers have NO row (exact 0.0 is never produced). Built by analysis.pe_and_dividends; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.dividends.sec_type        IS 'Security type as its stats.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity).';
COMMENT ON COLUMN analysis.dividends.code            IS 'Security code (bare index code e.g. 000300; ETF bare e.g. 159001; stock ticker with exchange suffix e.g. 600008.SS).';
COMMENT ON COLUMN analysis.dividends.date            IS 'Trading date.';
COMMENT ON COLUMN analysis.dividends.dividend_yield  IS 'Trailing-12m dividend yield (D/P) as a FRACTIONAL ratio (0.035 = 3.5%). Close is read live from stats at compute time (NOT stored in this table). stock: SUM(stats.stock_dividends.dividend_per_share_pre_tax WHERE ex_dividend_date in (date-365d, date]) / close. etf: SUM(stats.etf_adjustment.implied_dividend_per_share over trailing 365d) / close. index: cap-weighted average of constituent trailing-12m yields = SUM(weight_fraction x constituent dps / constituent close) using LATEST sec_composition snapshot (source_type=''index'', temporal extrapolation) — per-share numerator and denominator, a true yield (the former weighted-DPS / index-close formula mixed per-share CNY with index POINTS and understated yields ~100x). Strictly positive: rows with an undefined yield (close <= 0 or no dividend data in the trailing 365d window) are NOT inserted.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity (one entry per split table; the
--  legacy combined 'pe_and_dividends' entry is removed)
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pe', 'pe', NULL, NOW(),
     'Per-(sec_type, code, date) RAW PE: index from stats.index_valuation.pe, etf/stock from the builds.etf / builds.stock pre-computed stats tables; NULL-PE (<= 0 / no earnings) days have NO row. Split (2026-09) from the former combined analysis.pe_and_dividends. Built by analysis.pe_and_dividends (writes both split tables); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('dividends', 'dividends', NULL, NOW(),
     'Per-(sec_type, code, date) trailing-12m dividend yield (D/P, fractional): stock=SUM(stock_dividends.dividend_per_share_pre_tax over trailing 365d)/close; etf=SUM(etf_adjustment.implied_dividend_per_share over trailing 365d)/close; index=cap-weighted average of constituent trailing-12m yields using LATEST sec_composition snapshot (temporal extrapolation). Rows only where the yield is defined (strictly positive) — non-payers have no row. Split (2026-09) from the former combined analysis.pe_and_dividends. Built by analysis.pe_and_dividends (writes both split tables); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

DELETE FROM analysis.analysis_identity WHERE name = 'pe_and_dividends';

-- ============================================================================
--  PE & Dividend Stats — REMOVED (2026-09-28).
--
--  The former ANNUAL 10-year rolling stats snapshot tables
--  (analysis.pe_stats + analysis.dividends_stats — the 2026-09 split of
--  the combined analysis.pe_and_dividend_stats) are retired: their only
--  consumer was the UI's "Dividend Stats (10y rolling)" card on the
--  PE & Dividend page, which is gone. The daily tables above
--  (analysis.pe / analysis.dividends) and the monthly bands / streaks
--  tables below are unaffected.
-- ============================================================================

-- Drop the stats tables + their legacy combined predecessor (no-op on
-- fresh installs). Hash partitions and the is_active partial unique
-- indexes die with the tables.
DROP TABLE IF EXISTS analysis.pe_stats;
DROP TABLE IF EXISTS analysis.dividends_stats;
DROP TABLE IF EXISTS analysis.pe_and_dividend_stats;

DELETE FROM analysis.analysis_identity
WHERE name IN ('pe_stats', 'dividends_stats', 'pe_and_dividend_stats');


-- ============================================================================
--  PE percentile BANDS + band-BREAK excursion STREAKS
--  (2026-09: the former combined metric-discriminated
--  analysis.pe_and_dividend_pct / _pct_streaks tables split per metric;
--  2026-09-28: PE-only — the dividend_yield half, analysis.dividends_pct /
--  analysis.dividends_pct_streaks, was dropped with its unused consumer
--  path; the UI's Valuation Streaks card is PE-only) — the high/low
--  streaks pattern of analysis.mov_ave_high_low_pct[_streaks] applied to
--  the pe series:
--
--    Table: analysis.pe_pct         — monthly band of the pe series
--    Table: analysis.pe_pct_streaks — band-break streaks, pe series
--
--  Computation semantics are UNCHANGED: the pe series' own history
--  (analysis.pe) is banded (pe is NULL on no-earnings / invalid-PE days).
--  Both band legs are percentiles of the SAME series (no high/low legs —
--  a valuation metric has one value per day): low_val = the pct_type-th
--  percentile and high_val = the (100 - pct_type)-th percentile of the pe
--  values over the TRAILING (backward-only) window of `period` non-NULL
--  observations ending at the month's last observation row.
--
--  An excursion streak = a maximal consolidation of days whose pe value
--  stays ABOVE the day's own month-band high_val ("high" — PE is
--  stretched vs its own trailing history) or BELOW low_val ("low" — PE is
--  compressed), tolerating in-band re-entries of up to 5 consecutive
--  trading days (bridged). Day-level semantics are identical to
--  analysis.mov_ave_high_low_pct_streaks; see that table's header for the
--  full rationale (trailing-month moving bands, episode shift semantics,
--  wholesale rebuild, side derived at query time from the END month's
--  band).
-- ============================================================================

-- DROP the former combined tables (PK lost the metric column) and the
-- removed dividend_yield split halves. The pe bands are rebuilt by the
-- incremental detection (every triple is missing after the drop —
-- trailing windows are recomputed identically) and the streaks are
-- rebuilt wholesale on the next run that processes each sec_type. On
-- fresh installs these are no-ops.
DROP TABLE IF EXISTS analysis.pe_and_dividend_pct;
DROP TABLE IF EXISTS analysis.pe_and_dividend_pct_streaks;
DROP TABLE IF EXISTS analysis.dividends_pct;
DROP TABLE IF EXISTS analysis.dividends_pct_streaks;

-- ----------------------------------------------------------------------------
--  Table: analysis.pe_pct  (pe percentile band, monthly)
--    PK: (sec_type, code, date_year_month, period, pct_type)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.pe_pct (
    sec_type                        SMALLINT          NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code                            TEXT          NOT NULL,
    date_year_month                 DATE          NOT NULL,  -- band month FIRST day
    period                          INTEGER       NOT NULL,  -- 255, 500, 750, 1275
    pct_type                        INTEGER       NOT NULL,  -- 1 | 5 | 10 (percent)

    high_val                        NUMERIC(12,6) NOT NULL,  -- (100 - pct_type)-th pct of the window
    low_val                         NUMERIC(12,6) NOT NULL,  -- pct_type-th pct of the window

    CONSTRAINT pk_pe_pct PRIMARY KEY (sec_type, code, date_year_month, period, pct_type)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed on code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'pe_pct', 8);

CREATE INDEX IF NOT EXISTS idx_pe_pct_code
    ON analysis.pe_pct (sec_type, code, date_year_month);

COMMENT ON TABLE  analysis.pe_pct               IS 'Per-(sec_type, code, month, period, pct_type) percentile BAND of the pe series (2026-09 split of the former combined analysis.pe_and_dividend_pct — the metric column is gone; the table name carries the metric). low_val = pct_type-th percentile and high_val = (100 - pct_type)-th percentile (linear interpolation) of the pe series'' non-NULL values over the TRAILING window of `period` observations (255/500/750/1275 = ~1/2/3/5 trading years) ending at the month''s LAST observation row; both legs use the SAME series. One band row per calendar month, anchored at the month''s last observation row and stored under the month''s first day; the in-progress month is skipped (no true month-end anchor yet). Windows near a code''s history start are naturally truncated; fewer than 255 non-NULL pe observations yields no band. Trailing windows make completed months immutable — only missing (code, month) triples are computed incrementally (a triple is complete when all 12 period x pct_type rows exist); --force / single-code rebuilds the scope. Internal step of analysis.pe_and_dividends (pct_bands.py).';
COMMENT ON COLUMN analysis.pe_pct.sec_type      IS 'Security type: etf, index, or stock.';
COMMENT ON COLUMN analysis.pe_pct.code          IS 'Security code (bare index code e.g. 000300; ETF bare e.g. 159001; stock ticker with exchange suffix e.g. 600008.SS).';
COMMENT ON COLUMN analysis.pe_pct.date_year_month IS 'Calendar month of the band, stored as the month''s FIRST day (e.g. 2026-09-01) — the percentile window ENDS (inclusive) at the month''s last non-NULL observation row of the pe series. Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct.period        IS 'Lookback window length in pe observations: 255 / 500 / 750 / 1275 (~1 / 2 / 3 / 5 trading years). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct.pct_type      IS 'Band tightness in percent: 1 / 5 / 10. pct_type 1 = near-full range of the window ([1st, 99th] percentile); 10 = core envelope ([10th, 90th]). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct.high_val      IS '(100 - pct_type)-th percentile of the window''s pe values (band top; a day whose value rises ABOVE it is a high-side break).';
COMMENT ON COLUMN analysis.pe_pct.low_val       IS 'pct_type-th percentile of the window''s pe values (band bottom; a day whose value falls BELOW it is a low-side break).';

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
CREATE TABLE IF NOT EXISTS analysis.pe_pct_streaks (
    sec_type                        SMALLINT          NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code                            TEXT          NOT NULL,
    date_year_month                 DATE          NOT NULL,  -- the streak's START month FIRST day
    period                          INTEGER       NOT NULL,  -- 255, 500, 750, 1275
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
COMMENT ON COLUMN analysis.pe_pct_streaks.sec_type   IS 'Security type: etf, index, or stock.';
COMMENT ON COLUMN analysis.pe_pct_streaks.code       IS 'Security code (bare index code; ETF bare 6-digit; stock ticker with exchange suffix).';
COMMENT ON COLUMN analysis.pe_pct_streaks.date_year_month IS 'The streak''s START month, stored as the month''s FIRST day — the band-month context in which the excursion began. Streaks may span later months (each day is tested against its own month''s band). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.period     IS 'Lookback window of the audited band in observations: 255 / 500 / 750 / 1275 — the band''s period in analysis.pe_pct. Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.pct_type   IS 'Band tightness of the audited band in percent: 1 / 5 / 10 — the band''s pct_type in analysis.pe_pct. Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.start_date IS 'First observation day of the streak: the first OUT-OF-BAND day (pe above high_val or below low_val). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.end_date   IS 'Last observation day of the streak: the last OUT-OF-BAND day. Bridged in-band days (<= 5-day tolerated re-entries) lie strictly between start_date and end_date; trailing in-band days after end_date are NOT part of the streak (they may later become a bridged gap — hence the wholesale rebuild). Part of the PK.';
COMMENT ON COLUMN analysis.pe_pct_streaks.start_value IS 'The pe value on start_date (the streak''s first out-of-band day).';
COMMENT ON COLUMN analysis.pe_pct_streaks.end_value  IS 'The pe value on end_date (the streak''s last out-of-band day) — compared against the END month''s band at query time to derive the side.';
COMMENT ON COLUMN analysis.pe_pct_streaks.max_value  IS 'Maximum pe over all observation days in the streak span [start_date, end_date] (including bridged in-band days) — for a high streak this is the stretch''s peak stretch vs the trailing history.';
COMMENT ON COLUMN analysis.pe_pct_streaks.min_value  IS 'Minimum pe over all observation days in the streak span [start_date, end_date] (including bridged in-band days).';
COMMENT ON COLUMN analysis.pe_pct_streaks.day_count  IS 'Number of trading rows in the streak span [start_date, end_date] — out-of-band days plus bridged in-band days (tolerated <= 5-day re-entries).';
COMMENT ON COLUMN analysis.pe_pct_streaks.std_dev    IS 'Population standard deviation (ddof=0) of day-over-day pe changes within the streak span, in the metric''s own units (0.00 for single-day streaks).';

-- ----------------------------------------------------------------------------
--  Table: analysis.pe_codes  (per-code summary of pe ∪ dividends — ONE row
--  per (sec_type, code))
--
--  Precomputed source for the /api/analysis/pe/codes endpoint. The live
--  query it replaced aggregated analysis.pe ∪ analysis.dividends on every
--  request (UNION + sort + GROUP BY over 1.3M rows for index / 7.1M for
--  stock, plus DISTINCT-ON latest-row and identity-name lookups): 4.7s on
--  the index universe and 35s on the stock universe, gating the PE /
--  Dividends pages' first open (2026-09-29). This table moves that work
--  into the analysis.pe_and_dividends pipeline, where it runs offline once
--  per write pass; the endpoint becomes a small indexed table read
--  (~10ms). Same pattern as analysis.mov_ave_spreads_codes.
--
--  Maintained by analysis/pe_and_dividends/_common/codes_summary.py
--  (refresh_pe_codes_summary): DELETE + INSERT per affected sec_type (or
--  per --code in single-code runs) after every detail write pass.
--  Backfill = the same INSERT, run manually once per sec_type after
--  creating the table.
--
--  Column semantics mirror the former request-time query exactly:
--    first_date / last_date / n_dates — date coverage over the UNION of
--                                       the code's analysis.pe and
--                                       analysis.dividends rows
--    latest_pe / latest_dividend_yield — the metric value at the code's
--                                       last row in each metric table
--    name — latest identity name (from stats.{etf,index,stock}_identity)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.pe_codes (
    sec_type              SMALLINT         NOT NULL,  -- label code (stats.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code                  TEXT         NOT NULL,  -- matches pe/dividends.code (ETF bare; stock suffixed)
    name                  TEXT         NOT NULL DEFAULT '',
    first_date            DATE         NOT NULL,
    last_date             DATE         NOT NULL,
    n_dates               BIGINT       NOT NULL,

    latest_pe             NUMERIC(10,4),
    latest_dividend_yield NUMERIC(10,6),

    CONSTRAINT pk_pe_codes PRIMARY KEY (sec_type, code)
);

COMMENT ON TABLE  analysis.pe_codes IS 'Per-code summary of analysis.pe ∪ analysis.dividends (ONE row per sec_type+code): date coverage over the union, latest pe + dividend_yield snapshot, latest identity name. Precomputed source for the /api/analysis/pe/codes endpoint (the former live aggregate took 4.7s index / 35s stock per request). Maintained by analysis.pe_and_dividends after every detail write pass; the mov_ave_spreads_codes pattern.';
COMMENT ON COLUMN analysis.pe_codes.name                  IS 'Latest identity name (stats.{etf,index,stock}_identity row at the greatest date). Empty string when the identity table has no row for the code.';
COMMENT ON COLUMN analysis.pe_codes.n_dates               IS 'COUNT(DISTINCT date) over the UNION of the code''s analysis.pe and analysis.dividends rows (a date present in both counts once).';
COMMENT ON COLUMN analysis.pe_codes.latest_pe             IS 'pe at the code''s LAST analysis.pe row (NULL when the code has no pe rows).';
COMMENT ON COLUMN analysis.pe_codes.latest_dividend_yield IS 'dividend_yield at the code''s LAST analysis.dividends row (NULL when the code has no dividends rows).';

-- ----------------------------------------------------------------------------
--  Maintenance statement (the pipeline's refresh_pe_codes_summary runs this
--  per affected sec_type, with an optional code equality filter for
--  single-code runs; {identity} = stats.{etf,index,stock}_identity):
--
--  DELETE FROM analysis.pe_codes WHERE sec_type = $1 [AND code = $2];
--
--  INSERT INTO analysis.pe_codes (
--      sec_type, code, name, first_date, last_date, n_dates,
--      latest_pe, latest_dividend_yield)
--  WITH u AS (
--      SELECT code, date FROM analysis.pe WHERE sec_type = $1 [AND code = $2]
--      UNION
--      SELECT code, date FROM analysis.dividends WHERE sec_type = $1 [AND code = $2]
--  ), agg AS (
--      SELECT code, MIN(date) AS first_date, MAX(date) AS last_date,
--             COUNT(*) AS n_dates FROM u GROUP BY code
--  ), latest_pe AS (
--      SELECT DISTINCT ON (code) code, pe FROM analysis.pe
--       WHERE sec_type = $1 [AND code = $2] ORDER BY code, date DESC
--  ), latest_dy AS (
--      SELECT DISTINCT ON (code) code, dividend_yield FROM analysis.dividends
--       WHERE sec_type = $1 [AND code = $2] ORDER BY code, date DESC
--  )
--  SELECT $1, a.code, COALESCE(n.name, ''),
--         a.first_date, a.last_date, a.n_dates, lp.pe, ld.dividend_yield
--    FROM agg a
--    LEFT JOIN LATERAL (
--        SELECT x.name FROM {identity} x
--         WHERE x.code = a.code ORDER BY x.date DESC LIMIT 1 OFFSET 0
--    ) n ON TRUE
--    LEFT JOIN latest_pe lp ON lp.code = a.code
--    LEFT JOIN latest_dy ld ON ld.code = a.code;
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
--  Register the PE split steps in analysis.analysis_identity (the legacy
--  combined 'pe_and_dividend_pct' / 'pe_and_dividend_pct_streaks' entries
--  and the removed 'dividends_pct' / 'dividends_pct_streaks' entries are
--  removed)
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pe_pct', 'pe_pct', NULL, NOW(),
     'Per-(sec_type, code, month, period, pct_type) percentile BAND of the pe series from analysis.pe (the mov_ave_high_low_pct pattern applied to the valuation metric). low_val = pct_type-th and high_val = (100 - pct_type)-th percentile (linear interpolation) of the pe series'' non-NULL values over the TRAILING window of `period` observations (255/500/750/1275 = ~1/2/3/5 trading years) ending at the month''s last observation row; both legs use the same series. One band per calendar month stored under the month''s first day; the in-progress month is skipped; fewer than 255 non-NULL observations yields no band. Trailing windows make completed months immutable — only missing (code, month) triples are computed incrementally; --force / single-code rebuilds the scope. Internal step of analysis.pe_and_dividends (pct_bands.py).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pe_pct_streaks', 'pe_pct_streaks', NULL, NOW(),
     'Band-BREAK excursion streaks of the pe series audited against analysis.pe_pct (the mov_ave_high_low_pct_streaks pattern applied to the valuation metric): a day is OUT-OF-BAND when its pe value is ABOVE its own month-band high_val or BELOW low_val; an excursion streak is the maximal consolidation of same-side out-of-band trading days with in-band re-entries of up to 5 consecutive days bridged (longer gap or side switch ends it). start/end_value = pe on the first/last out-of-band day; max/min_value span the whole streak; day_count counts span trading rows. Non-trading and NULL-pe rows are excluded. Episodes shift with new data — rebuilt WHOLESALE per sec_type (per code in single-code mode) on every run that processes the scope. Side is derived at query time from the END month''s band. Internal step of analysis.pe_and_dividends (pct_streaks.py).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

DELETE FROM analysis.analysis_identity WHERE name IN (
    'pe_and_dividend_pct', 'pe_and_dividend_pct_streaks',
    'dividends_pct', 'dividends_pct_streaks');
