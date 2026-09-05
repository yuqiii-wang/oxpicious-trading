-- ============================================================================
--  PE & Dividend Yield — per-(sec_type, code, date) valuation analytics.
--
--  Close price and raw PE ratio are NOT stored here — they already live in
--  the stats schema (stats.index_basic_stats.close, stats.index_valuation.pe,
--  stats.etf_basic_stats.close, stats.stock_basic_stats.close). This table
--  stores ONLY derived analytics that are not present in stats:
--    • pe_ma20         — 20-trading-day moving average of PE (index-only)
--    • dividend_yield  — trailing-12m D/P (fractional ratio)
--
--  Mirrors analysis.mov_ave_spreads_detail's all-sec_types shape so the
--  tables join 1:1 on (sec_type, code, date).
--
--  Table: analysis.pe_and_dividends
--    PK: (sec_type, code, date)
--    sec_type ∈ ('index' | 'etf' | 'stock')
--
--  COLUMNS
--    pe_ma20         — 20-trading-day moving average of PE. Populated ONLY
--                      for sec_type='index' (computed from
--                      stats.index_valuation.pe via pandas rolling(20).mean(),
--                      min_periods=1, per code). NULL for etf/stock (no PE
--                      source). Not in stats.index_tech_stats (which has MA
--                      of close only, not MA of PE).
--
--    dividend_yield  — Trailing-12m dividend yield = (sum of per-share
--                      dividends going ex in the trailing 365-day window
--                      ending on `date`) / close. Stored as a FRACTIONAL
--                      ratio (0.035 = 3.5%), matching the convention used
--                      by analysis.mov_ave_spreads_detail gap columns and
--                      stats.index_tech_stats.ma5_ratio. NULL when close is
--                      NULL/<=0 or no dividend data falls in the window.
--                      Close is read live from stats at compute time (NOT
--                      stored in this table).
--
--                      Source per sec_type:
--                        stock -> SUM(stats.stock_dividends.
--                                     dividend_per_share_pre_tax) WHERE
--                                     code = stock_code AND ex_dividend_date
--                                     ∈ (date - 365d, date]. Pre-tax is used
--                                     for cross-issue comparability (tax
--                                     treatment varies by holding period).
--                        etf   -> SUM(stats.etf_adjustment.
--                                     implied_dividend_per_share) over the
--                                     trailing-12m ex-dividend events for
--                                     the ETF. implied_dividend_per_share is
--                                     the per-event increment (vs the running
--                                     cum_dividend_per_share), so the SUM
--                                     over the window = trailing-12m DPS.
--                        index -> Aggregated from CONSTITUENT STOCK dividends
--                                 weighted by composition — the CAP-WEIGHTED
--                                 AVERAGE of the constituents' own trailing-12m
--                                 YIELDS:
--                                   dividend_yield_t = SUM_s ( w_s × dps_s_t / close_s_t )
--                                 where s ranges over the index's constituent
--                                 stocks from the LATEST stats.sec_composition
--                                 snapshot (source_type='index',
--                                 temporal-extrapolation — same snapshot used
--                                 for all dates, mirroring industry_sentiments),
--                                 w_s = weight_pct_s / 100 (0..1), dps_s_t =
--                                 trailing-12m DPS of stock s, and close_s_t =
--                                 stock s's close on the date (both per-share).
--                                 UNIT NOTE: the former formula (weighted DPS /
--                                 index close) divided per-share CNY by index
--                                 POINTS — capitalization weights do not
--                                 convert between those units, so it
--                                 understated the yield by ~100x (the SSE
--                                 composite showed ~0.05% instead of ~2.5%).
--                                 NULL when the index has no composition
--                                 snapshot or no constituent has a trailing
--                                 DPS + close on the date.
--
--  POPULATION
--    analyze.pe_and_dividends (Python module, truncate-then-recompute on
--    every run). Per project rule, ALL INSERTs are in Python — no raw
--    INSERT...SELECT SQL in this file. For generic test runs, populate
--    sec_type='index' first; once verified, re-run for etf + stock.
--
--  Register in analysis.analysis_identity (name='pe_and_dividends').
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.pe_and_dividends (
    sec_type        TEXT         NOT NULL,  -- 'index' | 'etf' | 'stock'
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- 20-trading-day MA of PE (index-only; computed from stats.index_valuation.pe).
    -- Not in stats.index_tech_stats (which only has MA of close). NULL for etf/stock.
    pe_ma20         NUMERIC(10,4),

    -- Trailing-12m dividend yield (D/P) as a FRACTIONAL ratio (0.035 = 3.5%).
    -- NULL when close <= 0 or no dividend data in the trailing 365d window.
    dividend_yield  NUMERIC(10,6),

    CONSTRAINT pk_pe_and_dividends PRIMARY KEY (code, sec_type, date),
    CONSTRAINT chk_pe_and_dividends_sec_type
        CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15
SELECT public.create_hash_partitions('analysis', 'pe_and_dividends', 16);

-- Idempotent migration for existing DBs that already have the table from a
-- prior schema version (with close/pe columns). ADD COLUMN IF NOT EXISTS is
-- a no-op on fresh installs; close/pe columns from the prior version are
-- left in place (dropping columns is destructive and unnecessary — the build
-- script simply stops populating them).
ALTER TABLE analysis.pe_and_dividends
    ADD COLUMN IF NOT EXISTS pe_ma20        NUMERIC(10,4);
ALTER TABLE analysis.pe_and_dividends
    ADD COLUMN IF NOT EXISTS dividend_yield NUMERIC(10,6);

-- Indexes for the common access patterns:
--   1. Per-security time series (drives per-code valuation charts).
--   2. Per-date snapshot (drives the latest-date cross-sectional view).
--   3. sec_type-scoped scan (test runs populate sec_type='index' first).
-- idx_pe_and_dividends_code_sec_type_date (code, sec_type, date) dropped:
-- identical to the code-first PK, which already serves per-code lookups.
DROP INDEX IF EXISTS analysis.idx_pe_and_dividends_code_sec_type_date;
CREATE INDEX IF NOT EXISTS idx_pe_and_dividends_date
    ON analysis.pe_and_dividends (date);
CREATE INDEX IF NOT EXISTS idx_pe_and_dividends_sec_type_date
    ON analysis.pe_and_dividends (sec_type, date);

COMMENT ON TABLE  analysis.pe_and_dividends              IS 'Per-(code, sec_type, date) valuation analytics: pe_ma20 (20-day MA of PE, index-only) and dividend_yield (trailing-12m D/P, fractional ratio). Close and raw PE are NOT stored here — they live in stats (index_basic_stats.close, index_valuation.pe, etf_basic_stats.close, stock_basic_stats.close). Mirrors mov_ave_spreads_detail shape (joins 1:1 on sec_type, code, date). pe_ma20: pandas rolling(20).mean() of stats.index_valuation.pe per code (index-only, NULL for etf/stock). dividend_yield: stock=SUM(stock_dividends.dividend_per_share_pre_tax over trailing 365d)/close; etf=SUM(etf_adjustment.implied_dividend_per_share over trailing 365d)/close; index=cap-weighted average of constituent trailing-12m yields (SUM weight_fraction x dps/close, per-share numerator and denominator) using LATEST sec_composition snapshot (source_type=''index'', temporal extrapolation). Built by analyze.pe_and_dividends (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.pe_and_dividends.sec_type      IS 'Subject security type: index, etf, or stock. Determines which source PE/dividend tables apply (mirrors analysis.mov_ave_spreads_detail.sec_type).';
COMMENT ON COLUMN analysis.pe_and_dividends.code          IS 'Security code (bare index code e.g. 000300; ETF/stock ticker with exchange suffix e.g. 159001.SZ / 600008.SS).';
COMMENT ON COLUMN analysis.pe_and_dividends.date          IS 'Trading date.';
COMMENT ON COLUMN analysis.pe_and_dividends.pe_ma20       IS '20-trading-day moving average of PE. Index-only (computed from stats.index_valuation.pe via pandas rolling(20).mean(), min_periods=1, per code). NULL for etf/stock (no PE source table). Not in stats.index_tech_stats (which only has MA of close).';
COMMENT ON COLUMN analysis.pe_and_dividends.dividend_yield IS 'Trailing-12m dividend yield (D/P) as a FRACTIONAL ratio (0.035 = 3.5%). Close is read live from stats at compute time (NOT stored in this table). stock: SUM(stats.stock_dividends.dividend_per_share_pre_tax WHERE ex_dividend_date in (date-365d, date]) / close. etf: SUM(stats.etf_adjustment.implied_dividend_per_share over trailing 365d) / close. index: cap-weighted average of constituent trailing-12m yields = SUM(weight_fraction x constituent dps / constituent close) using LATEST sec_composition snapshot (source_type=''index'', temporal extrapolation) — per-share numerator and denominator, a true yield (the former weighted-DPS / index-close formula mixed per-share CNY with index POINTS and understated yields ~100x). Strictly positive or NULL: NULL when close <= 0 or no dividend data in the trailing 365d window (exact 0.0 is never produced — legacy 0.0 rows are stale pre-refactor values and are re-upserted by the build).';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pe_and_dividends', 'pe_and_dividends', NULL, NOW(),
     'Per-(sec_type, code, date) valuation analytics: pe_ma20 (20-day MA of PE, index-only, computed from stats.index_valuation.pe) and dividend_yield (trailing-12m D/P, fractional ratio). Close and raw PE are NOT stored (live in stats). dividend_yield: stock=SUM(stock_dividends.dividend_per_share_pre_tax over trailing 365d)/close; etf=SUM(etf_adjustment.implied_dividend_per_share over trailing 365d)/close; index=cap-weighted average of constituent trailing-12m yields (SUM weight_fraction x dps/close, per-share numerator and denominator) using LATEST sec_composition snapshot (source_type=index, temporal extrapolation). Built by analyze.pe_and_dividends (truncate-then-recompute); all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;


-- ============================================================================
--  PE & Dividend Stats — monthly 5-year rolling stats snapshot of PE and
--  dividend_yield, with an is_active flag for efficient "latest snapshot per
--  code" queries. Updated MONTHLY (one row per (sec_type, code, month-end
--  trading date)).
--
--  Table: analysis.pe_and_dividend_stats
--    PK: (sec_type, code, date, is_active)
--      date = month-end trading date (last trading day of each month)
--      is_active = TRUE for the most recent monthly snapshot per
--                  (sec_type, code); FALSE for all prior months. Part of the
--                  PK so a partial unique index can enforce "at most one
--                  latest per code" while keeping the flag queryable via
--                  the primary index.
--    sec_type ∈ ('index' | 'etf' | 'stock')
--
--  COLUMNS
--    min_pe_5y / max_pe_5y
--                   — Rolling 5-year (~1275 trading days) min / max of PE.
--                     Computed from stats.index_valuation.pe (index-only;
--                     NULL for etf/stock). The window ends on `date`. NULL
--                     when fewer than 1 non-NULL PE value exists in the
--                     window (e.g. new index with < 5y history — the window
--                     still computes over whatever history is available,
--                     matching pandas rolling(1275, min_periods=1)).
--
--    min_dividend_5y / max_dividend_5y  — REMOVED. The 5y min/max of
--                     dividend_yield were dropped in favor of the more
--                     informative dividend_var_5y (std) +
--                     last_dividend_per_share columns. Existing columns are
--                     dropped via the DROP TABLE + CREATE TABLE migration
--                     below.
--
--    dividend_var_5y
--                   — Rolling 5-year POPULATION std (ddof=0) of
--                     dividend_yield, scaled x100 to express it as a
--                     percentage (e.g. a fractional-yield std of 0.005
--                     becomes 0.5). Measures dispersion of the trailing-12m
--                     yield over the last 5y. NULL when fewer than 2 non-NULL
--                     dividend_yield values exist in the window (std
--                     undefined for a single observation).
--
--    last_dividend_per_share
--                   — Rolling record of the latest single dividend per
--                     share amount (dividend_per_share_pre_tax) as of the
--                     month-end date. For stock/etf this is the security's
--                     own most recent ex-dividend event on or before `date`
--                     (summed when multiple events share the same ex-date).
--                     NULL for index (the bare index code does not appear
--                     in stock_dividends) or when no dividend event exists
--                     on or before the month-end.
--
--    dividend_issued_this_month
--                   — TRUE if at least one ex_dividend_date falls in the
--                     same (year, month) as the month-end `date`. FALSE
--                     otherwise (including NULL/FALSE for index, which has
--                     no direct dividend events). Drives the bold styling
--                     on the Last Div cell in the UI.
--
--    dividend_stability_5y
--                   — Frequency-robust stability score (0-100) of the
--                     per-share dividend AMOUNT over the trailing 5
--                     CALENDAR YEARS. Measures dividend-POLICY consistency
--                     (NOT yield — yield conflates price moves with policy,
--                     so this column uses DPS amounts directly).
--
--                     FREQUENCY-CHANGE FIX (the reason this is NOT a naive
--                     per-payment comparison): dividends are summed to an
--                     ANNUAL TOTAL per calendar year before any comparison.
--                       stock: SUM(stats.stock_dividends.
--                                  dividend_per_share_pre_tax) WHERE
--                                  ex_dividend_date ∈ calendar year y.
--                       etf:   SUM(stats.etf_adjustment.
--                                  implied_dividend_per_share) over
--                                  ex-events in year y.
--                       index: SUM over constituents of (weight_fraction ×
--                                  constituent annual DPS), same aggregation
--                                  as the dividend_yield numerator but per
--                                  CALENDAR YEAR instead of trailing 365d.
--                     A year with 2 semi-annual payments of 0.50 and a year
--                     with 1 annual payment of 1.00 both annualize to 1.00,
--                     so there is NO artificial gap from dividing a single
--                     payment by 2 to force a "semi-annual equivalent".
--
--                     SCORE: CV = std(annual_dps_y) / mean(annual_dps_y)
--                     over years with non-zero annual_dps; stability =
--                     (1 - min(CV, 1)) × 100, clamped [0, 100]. 100 =
--                     perfectly stable (all years equal); 0 = highly
--                     variable (std >= mean). NULL when fewer than 2 years
--                     have non-zero annual_dps in the 5y window.
--
--  MONTHLY UPDATE
--    One row per (sec_type, code, month-end trading date). The build script
--    (analyze.pe_and_dividends.stats — internal step run_monthly_stats)
--    inserts a new row for the just-completed month and flips is_active:
--      1. UPDATE ... SET is_active = FALSE WHERE sec_type=? AND code=?
--         AND is_active = TRUE
--      2. INSERT new row with is_active = TRUE for the new month-end date
--    Run monthly (not daily) — the 5y rolling window is heavy and the
--    month-end snapshot is sufficient for valuation-band charts.
--
--  POPULATION
--    analyze.pe_and_dividends.stats (Python internal step). Per project
--    rule, ALL INSERTs/UPDATEs are in Python — no raw SQL in this file.
--    For generic test runs, populate sec_type='index' first.
--
--  Register in analysis.analysis_identity (name='pe_and_dividend_stats').
-- ============================================================================

-- DROP + recreate: the PK column was renamed from is_latest to is_active,
-- and ALTER cannot change an existing PK. The table holds no data at this
-- point (the Python populator has not run yet), so a clean rebuild is safe.
-- On fresh installs this is a no-op; on upgraded DBs it discards any prior
-- is_latest-based rows (acceptable — the table is rebuilt monthly anyway).
DROP TABLE IF EXISTS analysis.pe_and_dividend_stats;

CREATE TABLE IF NOT EXISTS analysis.pe_and_dividend_stats (
    sec_type            TEXT         NOT NULL,  -- 'index' | 'etf' | 'stock'
    code                TEXT         NOT NULL,
    date                DATE         NOT NULL,  -- month-end trading date
    is_active           BOOLEAN      NOT NULL DEFAULT FALSE,

    -- Rolling 5-year (~1275 trading days) min / max of PE (index-only).
    -- Computed from stats.index_valuation.pe. NULL for etf/stock or when
    -- no PE history exists in the window.
    min_pe_5y           NUMERIC(10,4),
    max_pe_5y           NUMERIC(10,4),

    -- Rolling 5-year POPULATION std (ddof=0) of dividend_yield, x100 as a
    -- percentage. NULL when fewer than 2 non-NULL values in the window.
    dividend_var_5y     NUMERIC(20,10),

    -- Frequency-robust stability score (0-100) of the per-share dividend
    -- AMOUNT over the trailing 5 CALENDAR YEARS (annualized per year so
    -- payment-frequency changes don't create artificial gaps). See header.
    dividend_stability_5y NUMERIC(6,2),

    -- Rolling record of the latest single dividend_per_share_pre_tax as of
    -- the month-end date (stock/etf own dividend events; NULL for index).
    last_dividend_per_share NUMERIC(18,6),

    -- TRUE if at least one ex_dividend_date falls in the same (year, month)
    -- as the month-end `date`. Drives bold styling on the Last Div cell.
    dividend_issued_this_month BOOLEAN NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_pe_and_dividend_stats PRIMARY KEY (code, sec_type, date, is_active),
    CONSTRAINT chk_pe_and_dividend_stats_sec_type
        CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'pe_and_dividend_stats', 8);

-- Idempotent migration for existing DBs. The DROP TABLE + CREATE TABLE
-- above handles fresh installs and the prior is_latest → is_active rename;
-- these ADD COLUMN IF NOT EXISTS statements cover any DB that already has
-- the table from a prior schema version (the min_dividend_5y /
-- max_dividend_5y columns from the prior version are left in place —
-- dropping columns is destructive and the Python populator no longer
-- writes to them).
ALTER TABLE analysis.pe_and_dividend_stats
    ADD COLUMN IF NOT EXISTS is_active        BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE analysis.pe_and_dividend_stats
    ADD COLUMN IF NOT EXISTS min_pe_5y        NUMERIC(10,4);
ALTER TABLE analysis.pe_and_dividend_stats
    ADD COLUMN IF NOT EXISTS max_pe_5y        NUMERIC(10,4);
ALTER TABLE analysis.pe_and_dividend_stats
    ADD COLUMN IF NOT EXISTS dividend_var_5y  NUMERIC(20,10);
ALTER TABLE analysis.pe_and_dividend_stats
    ADD COLUMN IF NOT EXISTS dividend_stability_5y NUMERIC(6,2);
ALTER TABLE analysis.pe_and_dividend_stats
    ADD COLUMN IF NOT EXISTS last_dividend_per_share NUMERIC(18,6);
ALTER TABLE analysis.pe_and_dividend_stats
    ADD COLUMN IF NOT EXISTS dividend_issued_this_month BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial unique index: at most ONE row per (sec_type, code) with is_active=TRUE.
-- Enforces the "single latest snapshot" invariant while allowing full monthly
-- history to accumulate. This is the index that makes is_active queryable
-- efficiently — "get latest stats for code X" resolves to a single index lookup.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pe_and_dividend_stats_latest
    ON analysis.pe_and_dividend_stats (sec_type, code)
    WHERE is_active = TRUE;

-- Secondary index for per-code time-series retrieval (all monthly snapshots
-- for a code, ignoring the is_active flag — drives the valuation-band chart).
-- idx_pe_and_dividend_stats_code_sec_type_date (code, sec_type, date) dropped:
-- a prefix of the code-first PK, which already serves per-code lookups.
DROP INDEX IF EXISTS analysis.idx_pe_and_dividend_stats_code_sec_type_date;

COMMENT ON TABLE  analysis.pe_and_dividend_stats                  IS 'Monthly 5-year rolling stats snapshot of PE and dividend_yield. One row per (code, sec_type, month-end trading date, is_active). is_active=TRUE for the most recent monthly snapshot per code (enforced by partial unique index uq_pe_and_dividend_stats_latest). min_pe_5y/max_pe_5y: rolling 5y (~1275 trading days) min/max of stats.index_valuation.pe (index-only, NULL for etf/stock). dividend_var_5y: rolling 5y population std (ddof=0) of dividend_yield x100 as a percentage. last_dividend_per_share: rolling record of the latest single dividend_per_share_pre_tax as of the month-end (stock/etf; NULL for index). dividend_issued_this_month: TRUE if any ex_dividend_date falls in the same (year, month) as the month-end. dividend_stability_5y: frequency-robust stability score (0-100) of per-share dividend AMOUNT over trailing 5 calendar years (annualized per year so payment-frequency changes do not create artificial gaps; CV-based: stability=(1-min(CV,1))×100). Updated MONTHLY by analyze.pe_and_dividends.stats (internal step). All INSERTs/UPDATEs in Python per project rule.';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.sec_type         IS 'Subject security type: index, etf, or stock.';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.code             IS 'Security code (bare index code e.g. 000300; ETF/stock ticker with exchange suffix e.g. 159001.SZ / 600008.SS).';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.date             IS 'Month-end trading date (last trading day of the month). One snapshot per month per (sec_type, code).';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.is_active        IS 'TRUE for the most recent monthly snapshot per (sec_type, code); FALSE for all prior months. Part of the PK and backed by partial unique index uq_pe_and_dividend_stats_latest (at most one TRUE per code). The build script flips prior is_active=TRUE to FALSE before inserting the new month''s row with is_active=TRUE.';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.min_pe_5y        IS 'Rolling 5-year (~1275 trading days) minimum of PE, ending on `date`. Computed from stats.index_valuation.pe (index-only; NULL for etf/stock). Window uses min_periods=1 so newer indices with < 5y history still get a value over available data. NULL when no non-NULL PE exists in the window.';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.max_pe_5y        IS 'Rolling 5-year (~1275 trading days) maximum of PE, ending on `date`. Computed from stats.index_valuation.pe (index-only; NULL for etf/stock). Window uses min_periods=1. NULL when no non-NULL PE exists in the window.';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.dividend_var_5y  IS 'Rolling 5-year POPULATION std (ddof=0) of analysis.pe_and_dividends.dividend_yield, ending on `date`, scaled x100 to express it as a percentage (e.g. a fractional-yield std of 0.005 becomes 0.5). Measures dispersion of the trailing-12m yield over the last 5y. Computed via pandas rolling(1275).std(ddof=0) per code (min_periods=2), then x100. NULL when fewer than 2 non-NULL dividend_yield values exist in the window (std undefined for a single observation).';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.dividend_stability_5y IS 'Frequency-robust stability score (0-100) of the per-share dividend AMOUNT over the trailing 5 CALENDAR YEARS ending on `date`. Measures dividend-POLICY consistency using DPS amounts directly (NOT yield — yield conflates price moves with policy, so this is distinct from dividend_var_5y). FREQUENCY-CHANGE FIX: dividends are summed to an ANNUAL TOTAL per calendar year before comparison, so a year with 2 semi-annual payments and a year with 1 annual payment are compared on equal footing (no artificial gap from dividing a single payment by 2 to force a semi-annual equivalent). stock: SUM(stock_dividends.dividend_per_share_pre_tax WHERE ex_dividend_date in year y); etf: SUM(etf_adjustment.implied_dividend_per_share over year y); index: SUM(weight_fraction × constituent annual DPS). SCORE: CV=std(annual_dps)/mean(annual_dps) over years with non-zero annual_dps; stability=(1-min(CV,1))×100 clamped [0,100]. 100=perfectly stable; 0=highly variable (std>=mean). NULL when fewer than 2 years have non-zero annual_dps in the 5y window.';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.last_dividend_per_share IS 'Rolling record of the latest single dividend_per_share_pre_tax as of the month-end `date`. For stock/etf: the security''s own most recent ex-dividend event on or before `date` (summed when multiple events share the same ex-date). For index: NULL (the bare index code does not appear in stock_dividends, so no dividend event matches). NULL when no dividend event exists on or before the month-end.';
COMMENT ON COLUMN analysis.pe_and_dividend_stats.dividend_issued_this_month IS 'TRUE if at least one ex_dividend_date falls in the same (year, month) as the month-end `date`. FALSE otherwise (including for index, which has no direct dividend events). Drives the bold styling on the Last Div cell in the UI.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pe_and_dividend_stats', 'pe_and_dividend_stats', NULL, NOW(),
     'Monthly 5-year rolling stats snapshot of PE and dividend_yield. One row per (sec_type, code, month-end trading date, is_active). is_active=TRUE for the most recent monthly snapshot per code (partial unique index enforces at most one TRUE per code). min_pe_5y/max_pe_5y: rolling 5y min/max of stats.index_valuation.pe (index-only). dividend_var_5y: rolling 5y population std (ddof=0) of dividend_yield x100 as a percentage. last_dividend_per_share: rolling record of the latest single dividend_per_share_pre_tax as of the month-end (stock/etf; NULL for index). dividend_issued_this_month: TRUE if any ex_dividend_date falls in the same (year, month) as the month-end. dividend_stability_5y: frequency-robust stability score (0-100) of per-share dividend AMOUNT over trailing 5 calendar years (annualized per year so payment-frequency changes do not create artificial gaps; CV-based: stability=(1-min(CV,1))×100). Updated MONTHLY by analyze.pe_and_dividends.stats (internal step). All INSERTs/UPDATEs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;


-- ============================================================================
--  PE & Dividend percentile BANDS + band-BREAK excursion STREAKS
--  (high/low streaks pattern of analysis.mov_ave_high_low_pct[_streaks]
--  applied to the two valuation series of analysis.pe_and_dividends).
--
--  metric ∈ ('pe_ma20' | 'dividend_yield') — the audited series is the
--  metric's own history in analysis.pe_and_dividends. Both band legs are
--  percentiles of the SAME series (no high/low legs — a valuation metric
--  has one value per day): low_val = the pct_type-th percentile and
--  high_val = the (100 - pct_type)-th percentile of the metric's values
--  over the TRAILING (backward-only) window of `period` non-NULL metric
--  observations ending at the month's last observation row.
--
--  An excursion streak = a maximal consolidation of days whose metric
--  value stays ABOVE the day's own month-band high_val ("high" — the
--  metric is stretched vs its own trailing history) or BELOW low_val
--  ("low" — the metric is compressed), tolerating in-band re-entries of
--  up to 5 consecutive trading days (bridged). Day-level semantics are
--  identical to analysis.mov_ave_high_low_pct_streaks; see that table's
--  header for the full rationale (trailing-month moving bands, episode
--  shift semantics, wholesale rebuild, side derived at query time from
--  the END month's band).
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.pe_and_dividend_pct  (metric percentile band, monthly)
--    PK: (sec_type, code, date_year_month, metric, period, pct_type)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis.pe_and_dividend_pct (
    sec_type                        TEXT          NOT NULL,  -- 'etf' | 'index' | 'stock'
    code                            TEXT          NOT NULL,
    date_year_month                 DATE          NOT NULL,  -- band month FIRST day
    metric                          TEXT          NOT NULL,  -- 'pe_ma20' | 'dividend_yield'
    period                          INTEGER       NOT NULL,  -- 255, 500, 750, 1275
    pct_type                        INTEGER       NOT NULL,  -- 1 | 5 | 10 (percent)

    high_val                        NUMERIC(12,6) NOT NULL,  -- (100 - pct_type)-th pct of the window
    low_val                         NUMERIC(12,6) NOT NULL,  -- pct_type-th pct of the window

    CONSTRAINT pk_pe_and_dividend_pct PRIMARY KEY (sec_type, code, date_year_month, metric, period, pct_type),
    CONSTRAINT chk_pe_and_dividend_pct_metric CHECK (metric IN ('pe_ma20', 'dividend_yield'))
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'pe_and_dividend_pct', 8);

CREATE INDEX IF NOT EXISTS idx_pe_and_dividend_pct_code
    ON analysis.pe_and_dividend_pct (sec_type, code, date_year_month);

COMMENT ON TABLE  analysis.pe_and_dividend_pct               IS 'Per-(sec_type, code, month, metric, period, pct_type) percentile BAND of a valuation metric (metric = pe_ma20 or dividend_yield from analysis.pe_and_dividends). low_val = pct_type-th percentile and high_val = (100 - pct_type)-th percentile (linear interpolation) of the metric''s non-NULL values over the TRAILING window of `period` observations (255/500/750/1275 = ~1/2/3/5 trading years) ending at the month''s LAST observation row; both legs use the SAME series (a valuation metric has one value per day — no high/low legs). One band row per calendar month, anchored at the month''s last observation row and stored under the month''s first day; the in-progress month is skipped (no true month-end anchor yet). Windows near a code''s history start are naturally truncated; fewer than 255 non-NULL observations of the metric yields no band. Trailing windows make completed months immutable — only missing (code, month, metric) triples are computed incrementally (a triple is complete when all 12 period×pct_type rows exist); --force / single-code rebuilds the scope. Internal step of analyze.pe_and_dividends (pct_bands.py).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct.sec_type      IS 'Security type: etf, index, or stock (mirrors analysis.pe_and_dividends.sec_type).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct.code          IS 'Security code (bare index code e.g. 000300; ETF/stock ticker with exchange suffix e.g. 159001.SZ / 600008.SS).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct.date_year_month IS 'Calendar month of the band, stored as the month''s FIRST day (e.g. 2026-09-01) — the percentile window ENDS (inclusive) at the month''s last non-NULL observation row of the metric. Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct.metric        IS 'Audited valuation series: pe_ma20 (20-day MA of PE) or dividend_yield (trailing-12m D/P, fractional). Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct.period        IS 'Lookback window length in metric observations: 255 / 500 / 750 / 1275 (~1 / 2 / 3 / 5 trading years). Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct.pct_type      IS 'Band tightness in percent: 1 / 5 / 10. pct_type 1 = near-full range of the window ([1st, 99th] percentile); 10 = core envelope ([10th, 90th]). Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct.high_val      IS '(100 - pct_type)-th percentile of the window''s metric values (band top; a day whose value rises ABOVE it is a high-side break).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct.low_val       IS 'pct_type-th percentile of the window''s metric values (band bottom; a day whose value falls BELOW it is a low-side break).';

-- ----------------------------------------------------------------------------
--  Table: analysis.pe_and_dividend_pct_streaks  (band-break excursion streaks)
--    PK: (sec_type, code, date_year_month, metric, period, pct_type,
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
CREATE TABLE IF NOT EXISTS analysis.pe_and_dividend_pct_streaks (
    sec_type                        TEXT          NOT NULL,  -- 'etf' | 'index' | 'stock'
    code                            TEXT          NOT NULL,
    date_year_month                 DATE          NOT NULL,  -- the streak's START month FIRST day
    metric                          TEXT          NOT NULL,  -- 'pe_ma20' | 'dividend_yield'
    period                          INTEGER       NOT NULL,  -- 255, 500, 750, 1275
    pct_type                        INTEGER       NOT NULL,  -- 1 | 5 | 10 (percent)

    start_date                      DATE          NOT NULL,  -- first OUT-OF-BAND observation day
    end_date                        DATE          NOT NULL,  -- last OUT-OF-BAND observation day
    start_value                     NUMERIC(12,6) NOT NULL,  -- the metric value on start_date
    end_value                       NUMERIC(12,6) NOT NULL,  -- the metric value on end_date
    max_value                       NUMERIC(12,6) NOT NULL,  -- max metric value over the span
    min_value                       NUMERIC(12,6) NOT NULL,  -- min metric value over the span
    day_count                       INTEGER       NOT NULL,  -- trading rows in [start_date, end_date]
    std_dev                         NUMERIC(12,6) NOT NULL,  -- population std of day-over-day value changes

    CONSTRAINT pk_pe_and_dividend_pct_streaks PRIMARY KEY (sec_type, code, date_year_month, metric, period, pct_type, start_date, end_date),
    CONSTRAINT chk_pe_and_dividend_pct_streaks_metric CHECK (metric IN ('pe_ma20', 'dividend_yield'))
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'pe_and_dividend_pct_streaks', 8);

CREATE INDEX IF NOT EXISTS idx_pe_and_dividend_pct_streaks_code
    ON analysis.pe_and_dividend_pct_streaks (sec_type, code, metric, period, pct_type, start_date);

COMMENT ON TABLE  analysis.pe_and_dividend_pct_streaks            IS 'Band-BREAK excursion streaks audited against analysis.pe_and_dividend_pct (the high/low streaks pattern applied to pe_ma20 / dividend_yield). A day is OUT-OF-BAND when its metric value (pe_ma20 or dividend_yield) is ABOVE its own month-band high_val or BELOW low_val. An excursion streak is the maximal consolidation of same-side out-of-band TRADING days where re-entries of up to 5 consecutive trading days are TOLERATED (bridged — in-band gap days stay inside the span and count in day_count); a longer in-band gap or a side switch ends the streak. start_date/end_date bound the span (first/last OUT-OF-BAND day). Non-trading vendor rows (ffilled weekday holidays) and NULL-metric rows are excluded before classification — spans, gap tolerance and day_count are in REAL observation days. Streaks can span calendar months (each day is tested against its OWN month''s band); date_year_month records the START month. Episodes shift with new data, so the table is rebuilt WHOLESALE per sec_type (per code in single-code mode) on every run that processes the scope. The side (high/low) is derived at query time from the END month''s band (a streak never switches sides). Internal step of analyze.pe_and_dividends (pct_streaks.py).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.sec_type   IS 'Security type: etf, index, or stock (mirrors analysis.pe_and_dividends.sec_type).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.code       IS 'Security code (bare index code; ETF/stock ticker with exchange suffix).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.date_year_month IS 'The streak''s START month, stored as the month''s FIRST day — the band-month context in which the excursion began. Streaks may span later months (each day is tested against its own month''s band). Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.metric     IS 'Audited valuation series: pe_ma20 or dividend_yield. Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.period     IS 'Lookback window of the audited band in observations: 255 / 500 / 750 / 1275 — the band''s period in analysis.pe_and_dividend_pct. Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.pct_type   IS 'Band tightness of the audited band in percent: 1 / 5 / 10 — the band''s pct_type in analysis.pe_and_dividend_pct. Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.start_date IS 'First observation day of the streak: the first OUT-OF-BAND day (metric value above high_val or below low_val). Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.end_date   IS 'Last observation day of the streak: the last OUT-OF-BAND day. Bridged in-band days (<= 5-day tolerated re-entries) lie strictly between start_date and end_date; trailing in-band days after end_date are NOT part of the streak (they may later become a bridged gap — hence the wholesale rebuild). Part of the PK.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.start_value IS 'The metric''s value on start_date (the streak''s first out-of-band day).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.end_value  IS 'The metric''s value on end_date (the streak''s last out-of-band day) — compared against the END month''s band at query time to derive the side.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.max_value  IS 'Maximum metric value over all observation days in the streak span [start_date, end_date] (including bridged in-band days) — for a high streak this is the stretch''s peak stretch vs the trailing history.';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.min_value  IS 'Minimum metric value over all observation days in the streak span [start_date, end_date] (including bridged in-band days).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.day_count  IS 'Number of trading rows in the streak span [start_date, end_date] — out-of-band days plus bridged in-band days (tolerated <= 5-day re-entries).';
COMMENT ON COLUMN analysis.pe_and_dividend_pct_streaks.std_dev    IS 'Population standard deviation (ddof=0) of day-over-day metric-value changes within the streak span, in the metric''s own units (0.00 for single-day streaks).';

-- ----------------------------------------------------------------------------
--  Register both steps in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pe_and_dividend_pct', 'pe_and_dividend_pct', NULL, NOW(),
     'Per-(sec_type, code, month, metric, period, pct_type) percentile BAND of the pe_ma20 / dividend_yield series from analysis.pe_and_dividends. low_val = pct_type-th and high_val = (100 - pct_type)-th percentile (linear interpolation) of the metric''s non-NULL values over the TRAILING window of `period` observations (255/500/750/1275 = ~1/2/3/5 trading years) ending at the month''s last observation row; both legs use the same series. One band per calendar month stored under the month''s first day; the in-progress month is skipped; fewer than 255 non-NULL observations yields no band. Trailing windows make completed months immutable — only missing (code, month, metric) triples are computed incrementally; --force / single-code rebuilds the scope. Internal step of analyze.pe_and_dividends (pct_bands.py).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('pe_and_dividend_pct_streaks', 'pe_and_dividend_pct_streaks', NULL, NOW(),
     'Band-BREAK excursion streaks audited against analysis.pe_and_dividend_pct: a day is OUT-OF-BAND when its metric value (pe_ma20 / dividend_yield) is ABOVE its own month-band high_val or BELOW low_val; an excursion streak is the maximal consolidation of same-side out-of-band trading days with in-band re-entries of up to 5 consecutive days bridged (longer gap or side switch ends it). start/end_value = metric value on the first/last out-of-band day; max/min_value span the whole streak; day_count counts span trading rows. Non-trading and NULL-metric rows are excluded. Episodes shift with new data — rebuilt WHOLESALE per sec_type (per code in single-code mode) on every run that processes the scope. Side is derived at query time from the END month''s band. Internal step of analyze.pe_and_dividends (pct_streaks.py).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
