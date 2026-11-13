
-- ----------------------------------------------------------------------------
--  Table: analysis.dividends  (daily trailing-12m dividend yield)
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each security's trailing-twelve-month dividend yield per day.
-- tags: dividend yield, 股息率, dividend, 分红

CREATE TABLE IF NOT EXISTS analysis.dividends (
    sec_type        SMALLINT         NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- Trailing-12m dividend yield (D/P) as a FRACTIONAL ratio
    -- (0.035 = 3.5%). Strictly positive — non-payers have NO row.
    dividend_yield  NUMERIC(10,6) NOT NULL,

    CONSTRAINT pk_dividends PRIMARY KEY (code, sec_type, date),
    CONSTRAINT chk_dividends_sec_type
        CHECK (sec_type IN (0, 1, 2, 3))  -- label codes: 0=etf 1=index 2=stock 3=industry
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'dividends', 16);

CREATE INDEX IF NOT EXISTS idx_dividends_date
    ON analysis.dividends (date);
CREATE INDEX IF NOT EXISTS idx_dividends_sec_type_date
    ON analysis.dividends (sec_type, date);

COMMENT ON TABLE  analysis.dividends        IS 'Per-(code, sec_type, date) trailing-12m dividend yield (D/P, fractional ratio; 2026-09 split from the former combined analysis.pe_and_dividends). Close is read live from stats at compute time (NOT stored here). stock: SUM(stock_dividends.dividend_per_share_pre_tax over trailing 365d)/close; etf: SUM(etf_adjustment.implied_dividend_per_share over trailing 365d)/close; index: cap-weighted average of constituent trailing-12m yields (SUM weight_fraction x dps/close, per-share numerator and denominator) using LATEST sec_composition snapshot (source_type=''index'', temporal extrapolation); industry: cross-sectional MEAN of the member indices'' own yields (analysis.dividends sec_type=1) — the same AVG-across-members convention as stats.industry_basic_stats.mean_pe (a yield is a ratio, scale-invariant). Rows exist only where the yield is defined (strictly positive) — non-payers have NO row (exact 0.0 is never produced). Built by analysis.pe_and_dividends; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.dividends.sec_type        IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity), 3 = industry (composite industry, code = stats.sec_classification.industry_id).';
COMMENT ON COLUMN analysis.dividends.code            IS 'Security code (bare index code e.g. 000300; ETF bare e.g. 159001; stock ticker with exchange suffix e.g. 600008.SS; sec_type=3: industry_id e.g. BANKS).';
COMMENT ON COLUMN analysis.dividends.date            IS 'Trading date.';
COMMENT ON COLUMN analysis.dividends.dividend_yield  IS 'Trailing-12m dividend yield (D/P) as a FRACTIONAL ratio (0.035 = 3.5%). Close is read live from stats at compute time (NOT stored in this table). stock: SUM(stats.stock_dividends.dividend_per_share_pre_tax WHERE ex_dividend_date in (date-365d, date]) / close. etf: SUM(stats.etf_adjustment.implied_dividend_per_share over trailing 365d) / close. index: cap-weighted average of constituent trailing-12m yields = SUM(weight_fraction x constituent dps / constituent close) using LATEST sec_composition snapshot (source_type=''index'', temporal extrapolation) — per-share numerator and denominator, a true yield (the former weighted-DPS / index-close formula mixed per-share CNY with index POINTS and understated yields ~100x). industry: cross-sectional MEAN of the member indices'' own dividend_yield values (analysis.dividends sec_type=1; members = stats.sec_classification type=''index'' is_active rows with that industry_id), NULL-excluded per member — no member yield on a date means NO industry row. Strictly positive: rows with an undefined yield are NOT inserted.';

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
