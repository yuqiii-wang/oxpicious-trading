-- ============================================================================
--  Performance Attribution — daily composition overlap + ETF-market liquidity
--  + rolling close-price correlation between a subject security and each
--  benchmark index.
--
--  Table: sec_alloc_perf_attribution
--    Stores holdings-based composition comparison (shared weight overlap),
--    ETF-market turnover (benchmark vs subject), and rolling close-price
--    correlations.  Former return-decomposition columns (subject_return,
--    benchmark_return, active_return, allocation_effect) have been REMOVED —
--  returns are derivable on the fly from close prices if ever needed.
--
--  Subject types (sec_type):
--    'stock'  — individual equity (code = "000001.SZ" etc.)
--    'etf'    — ETF (code = "510050.SS" etc.)
--    'index'  — sub-index or broad-market index (code = "930606" or "000300" etc.)
--
--  Benchmark (benchmark_code): any index code. Typically one of 6 broad-market
--    indices but not constrained:
--    000300 沪深300  · 000001 上证指数 · 000852 中证1000
--    399001 深证成指 · 399006 创业板指 · 000688 科创50
--
--  Composition correlation (NULL for stocks — no internal holdings):
--    code_sec_shared_weight      = Σ w_subject   on shared (overlapping) stocks
--    benchmark_sec_shared_weight = Σ w_benchmark on shared stocks
--
--  ETF-MARKET AMOUNT:
--    benchmark_etf_trading_amount = Σ etf_liquidity_margin.trading_amount across
--                           ALL ETFs tracking benchmark_code on this date
--                           (parent_index_code = benchmark_code in
--                           stats.sec_classification). NULL when no ETF
--                           tracks the benchmark.
--    code_etf_trading_amount      = subject's own turnover (etf_liquidity_margin.trading_amount)
--                           when sec_type='etf'; aggregate ETF turnover
--                           tracking the subject index when sec_type='index'.
--    etf_trading_amount_ratio_benchmark_to_code = GENERATED column =
--      benchmark_etf_trading_amount / code_etf_trading_amount (NULL when either is NULL/0).
--      A ratio ≥ 1 means the benchmark's ETF-market turnover is larger than
--      the subject's. The INVERSE (code_etf_trading_amount / benchmark_etf_trading_amount)
--      is the subject's SHARE of the benchmark's ETF market and is the
--      interpretable "proportion" form — computed in the UI as 1/ratio.
--
--  STATISTICAL ATTRIBUTION (rolling correlations):
--    corr_5d / corr_20d / corr_60d / corr_255d = rolling Pearson correlation
--      of subject close vs benchmark close over trailing N trading days.
-- ============================================================================

-- DROP + recreate whenever this file is re-run (schema changes require it;
-- the analyze script truncates+reinserts so no data is lost).
DROP TABLE IF EXISTS analysis.sec_alloc_perf_attribution CASCADE;

-- ----------------------------------------------------------------------------
--  Table: analysis.sec_alloc_perf_attribution
--  PK: (code, date, sec_type, benchmark_code)
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.sec_alloc_perf_attribution (
    code                    TEXT      NOT NULL,
    date                    DATE      NOT NULL,
    sec_type                TEXT      NOT NULL,  -- 'stock' | 'etf' | 'index'
    benchmark_code          TEXT      NOT NULL,

    code_sec_shared_weight         NUMERIC(8,4),  -- Σ w_subject   on shared stocks
    benchmark_sec_shared_weight    NUMERIC(8,4),  -- Σ w_benchmark on shared stocks

    -- ETF-market trading amount aggregated across ALL ETFs tracking the benchmark
    -- index (via stats.sec_classification.parent_index_code). NULL when no
    -- ETF tracks the benchmark (e.g. broad indices like 上证指数 000001).
    benchmark_etf_trading_amount               NUMERIC(16,2),  -- Σ etf trading_amount for ETFs tracking benchmark_code
    -- Subject's own ETF trading_amount (sec_type='etf') OR aggregate ETF trading_amount
    -- tracking the subject index (sec_type='index'). NULL for stocks.
    code_etf_trading_amount                    NUMERIC(16,2),
    etf_trading_amount_ratio_benchmark_to_code NUMERIC(10,4)
        GENERATED ALWAYS AS (
            CASE
                WHEN benchmark_etf_trading_amount IS NULL OR code_etf_trading_amount IS NULL
                  OR benchmark_etf_trading_amount = 0 OR code_etf_trading_amount = 0
                THEN NULL
                -- Cap at NUMERIC(10,4) max (|ratio| < 10^6). Ratios
                -- exceeding this (tiny subject ETF trading amount vs a large
                -- benchmark) are NULL'd to avoid overflow — see the
                -- matching cap in analyze_sec_alloc_perf_attribution.py.
                WHEN ABS(benchmark_etf_trading_amount / code_etf_trading_amount) >= 1000000
                THEN NULL
                ELSE benchmark_etf_trading_amount / code_etf_trading_amount
            END
        ) STORED,
    -- 5-trading-day moving average of etf_trading_amount_ratio_benchmark_to_code,
    -- populated by analyze_sec_alloc_perf_attribution.py via pandas
    -- rolling(5).mean() per (code, sec_type, benchmark_code) group.
    etf_trading_amount_ratio_benchmark_to_code_ma5 NUMERIC(10,4),

    corr_5d                NUMERIC(8,4),  -- 5-day close correlation between subject and benchmark
    corr_20d               NUMERIC(8,4),  -- 20-day close correlation between subject and benchmark
    corr_60d               NUMERIC(8,4),  -- 60-day close correlation between subject and benchmark
    corr_255d              NUMERIC(8,4),  -- 255-day close correlation between subject and benchmark

    CONSTRAINT pk_sec_alloc_perf_attribution
        PRIMARY KEY (code, date, sec_type, benchmark_code),
    CONSTRAINT chk_sec_perf_attr_sec_type
        CHECK (sec_type IN ('stock', 'etf', 'index')),
    CONSTRAINT chk_sec_perf_attr_code_format CHECK (
        (sec_type = 'stock' AND code ~ '^\d{6}\.(SZ|SS|BJ)$')
        OR (sec_type = 'etf'   AND code ~ '^\d{6}\.(SZ|SS|SH)$')
        OR (sec_type = 'index' AND code ~ '^(\d{6}|H\d{5})$')
    )
);

CREATE INDEX idx_sec_perf_attr_date_code_benchmark
    ON analysis.sec_alloc_perf_attribution (date, code, benchmark_code);
CREATE INDEX idx_sec_perf_attr_sec_type_date
    ON analysis.sec_alloc_perf_attribution (sec_type, date);

COMMENT ON TABLE  analysis.sec_alloc_perf_attribution                  IS 'Daily composition overlap + ETF-market liquidity + rolling close correlations: one row per (code, date, sec_type, benchmark_code). Stores composition overlap metrics (code_sec_shared_weight, benchmark_sec_shared_weight), ETF-market turnover (benchmark_etf_trading_amount, code_etf_trading_amount, etf_trading_amount_ratio_benchmark_to_code), and rolling close correlations (corr_5d/20d/60d/255d). sec_type ∈ {stock, etf, index}. Composition and ETF trading_amount columns are NULL for stocks.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.sec_type         IS 'Subject security type: stock, etf, or index. Determines which source price table and (for etf/index) which composition source applies.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_code IS 'Benchmark index code (typically one of the 6 broad-market indices: 000300, 000001, 000852, 399001, 399006, 000688, but not constrained).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.code_sec_shared_weight         IS 'Σ w_subject on stocks held by BOTH securities. The fraction of the subject''s weight that overlaps with the benchmark. NULL for stocks (no internal holdings).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_sec_shared_weight    IS 'Σ w_benchmark on stocks held by BOTH securities. The fraction of the benchmark''s weight that overlaps with the subject. NULL for stocks (no internal holdings).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_etf_trading_amount           IS 'Aggregate ETF turnover (yuan) on this date across ALL ETFs tracking benchmark_code. Source: Σ stats.etf_liquidity_margin.trading_amount where the ETF''s stats.sec_classification.parent_index_code = benchmark_code. NULL when no ETF tracks the benchmark (e.g. 上证指数 000001 has no direct ETF tracking it).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.code_etf_trading_amount                IS 'Subject''s ETF turnover (yuan). For sec_type=''etf'': the ETF''s own stats.etf_liquidity_margin.trading_amount. For sec_type=''index'': aggregate ETF turnover tracking the subject index (same aggregation as benchmark_etf_trading_amount but keyed on subject code). NULL for stocks and for indices with no tracking ETF.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.etf_trading_amount_ratio_benchmark_to_code IS 'GENERATED ALWAYS AS (CASE WHEN benchmark_etf_trading_amount IS NULL OR code_etf_trading_amount IS NULL OR benchmark_etf_trading_amount = 0 OR code_etf_trading_amount = 0 THEN NULL WHEN ABS(benchmark_etf_trading_amount / code_etf_trading_amount) >= 1000000 THEN NULL ELSE benchmark_etf_trading_amount / code_etf_trading_amount END). Ratio ≥ 1 means benchmark''s ETF-market turnover exceeds subject''s. NOTE: this is a LIQUIDITY ratio, not a price-attribution proportion. The subject''s SHARE of the benchmark ETF market = 1 / etf_trading_amount_ratio_benchmark_to_code (computed in UI). Capped at |ratio| < 10^6 to fit NUMERIC(10,4); larger ratios (tiny subject ETF turnover vs a large benchmark) are NULL''d — see matching cap in analyze_sec_alloc_perf_attribution.py.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.etf_trading_amount_ratio_benchmark_to_code_ma5 IS '5-trading-day moving average of etf_trading_amount_ratio_benchmark_to_code. Populated by analyze_sec_alloc_perf_attribution.py via pandas rolling(5).mean() per (code, sec_type, benchmark_code) group (min_periods=1, so the first 4 days of each series use a partial average). NULL when the underlying ratio is NULL for the entire trailing 5-day window. Smooths the noisy daily liquidity ratio so the UI can show a stable trend alongside the raw daily value.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.corr_5d                IS 'Rolling Pearson correlation of subject close vs benchmark close over trailing 5 trading days (min_periods ≈ 2N/3). NULL when insufficient non-NaN data.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.corr_20d               IS 'Rolling Pearson correlation of subject close vs benchmark close over trailing 20 trading days (min_periods ≈ 2N/3).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.corr_60d               IS 'Rolling Pearson correlation of subject close vs benchmark close over trailing 60 trading days (min_periods ≈ 2N/3).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.corr_255d              IS 'Rolling Pearson correlation of subject close vs benchmark close over trailing 255 trading days (min_periods ≈ 2N/3).';


-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('sec_alloc_perf_attribution', 'sec_alloc_perf_attribution', NULL, NOW(),
     'Daily composition overlap + ETF-market liquidity + rolling close correlations across stocks, ETFs, and sub-indices. Stores code_sec_shared_weight / benchmark_sec_shared_weight (composition overlap from stats.sec_composition), benchmark_etf_trading_amount / code_etf_trading_amount (ETF-market turnover from stats.index_exts), etf_trading_amount_ratio_benchmark_to_code (GENERATED liquidity ratio), and corr_5d/20d/60d/255d (rolling Pearson close-price correlations). Composition and ETF trading_amount columns are NULL for stocks.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
