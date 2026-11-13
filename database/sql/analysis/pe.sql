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
--  stats.stock_basic_stats.close, stats.industry_basic_stats.mean_close).
--  Both tables mirror
--  analysis.mov_ave_spreads_detail's all-sec_types shape so they join 1:1
--  on (sec_type, code, date).
--
--    sec_type ∈ ('index' | 'etf' | 'stock' | 'industry')
--
--  ============================================================================
--  Table: analysis.pe
--    PK: (sec_type, code, date)
--
--  COLUMNS
--    pe — RAW PE (no smoothing): index from stats.index_valuation.pe; etf
--         from stats.etf_basic_stats.pe (pre-computed by builds.etf via
--         harmonic-weighted constituent PE); stock from
--         stats.stock_basic_stats.pe (pre-computed by builds.stock);
--         industry from stats.industry_basic_stats.mean_pe (pool 'all' —
--         the build's cross-sectional AVG of member-index raw PE). NULL
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
--                                for all dates, mirroring market_beta),
--                                w_s = weight_pct_s / 100 (0..1), dps_s_t =
--                                trailing-12m DPS of stock s, and close_s_t =
--                                stock s's close on the date (both per-share).
--                                UNIT NOTE: the former formula (weighted DPS /
--                                index close) divided per-share CNY by index
--                                POINTS — capitalization weights do not
--                                convert between those units, so it
--                                understated the yield by ~100x (the SSE
--                                composite showed ~0.05% instead of ~2.5%).
--                       industry -> Cross-sectional MEAN of the member
--                                indices' own dividend_yield values
--                                (analysis.dividends sec_type=1), members =
--                                stats.sec_classification rows
--                                (type='index', is_active) carrying that
--                                industry_id — the same AVG-across-members
--                                convention stats.industry_basic_stats uses
--                                for mean_pe (a yield is a ratio,
--                                scale-invariant; NULL members excluded).
--                                Depends on the index side of THIS pipeline
--                                having run (the industry processor runs
--                                last).
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
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each security's daily price-to-earnings ratio, unified across index, ETF, stock and composite-industry sources.
-- tags: pe, 市盈率, valuation, 估值

CREATE TABLE IF NOT EXISTS analysis.pe (
    sec_type        SMALLINT         NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- Raw PE (all sec_types — see the source table per sec_type above).
    -- Strictly positive or NULL... never stored: rows with an invalid
    -- (<= 0 / NULL) source PE are NOT inserted at all.
    pe              NUMERIC(10,4) NOT NULL,

    CONSTRAINT pk_pe PRIMARY KEY (code, sec_type, date),
    CONSTRAINT chk_pe_sec_type
        CHECK (sec_type IN (0, 1, 2, 3))  -- label codes: 0=etf 1=index 2=stock 3=industry
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis', 'pe', 16);

CREATE INDEX IF NOT EXISTS idx_pe_date
    ON analysis.pe (date);
CREATE INDEX IF NOT EXISTS idx_pe_sec_type_date
    ON analysis.pe (sec_type, date);

COMMENT ON TABLE  analysis.pe        IS 'Per-(code, sec_type, date) RAW price-to-earnings ratio (no smoothing; 2026-09 split from the former combined analysis.pe_and_dividends). Sources per sec_type: index = stats.index_valuation.pe; etf = stats.etf_basic_stats.pe (pre-computed by builds.etf via harmonic-weighted constituent PE); stock = stats.stock_basic_stats.pe (pre-computed by builds.stock); industry = stats.industry_basic_stats.mean_pe (pool ''all'' — the build''s cross-sectional AVG of member-index raw PE, already a ratio so no rebasing). Rows exist only for source days with a valid (strictly positive) PE — invalid (<= 0 / NULL) PE days have NO row. Built by analysis.pe_and_dividends; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.pe.sec_type IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity), 3 = industry (composite industry, code = stats.sec_classification.industry_id).';
COMMENT ON COLUMN analysis.pe.code     IS 'Security code (bare index code e.g. 000300; ETF bare e.g. 159001; stock ticker with exchange suffix e.g. 600008.SS; sec_type=3: industry_id e.g. BANKS).';
COMMENT ON COLUMN analysis.pe.date     IS 'Trading date.';
COMMENT ON COLUMN analysis.pe.pe       IS 'RAW price-to-earnings ratio, no smoothing (2026-09: replaced the legacy pe_ma20 20-day MA, then the combined table split per metric). Sources per sec_type: index = stats.index_valuation.pe; etf = stats.etf_basic_stats.pe (pre-computed by builds.etf via harmonic-weighted constituent PE); stock = stats.stock_basic_stats.pe (pre-computed by builds.stock); industry = stats.industry_basic_stats.mean_pe (pool ''all'' slice — cross-sectional mean of member-index raw PE; PE is scale-invariant so the mean uses RAW values, no rebasing).';
