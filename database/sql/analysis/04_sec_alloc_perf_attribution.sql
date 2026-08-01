-- ============================================================================
--  Performance Attribution — unified daily decomposition of a security's
--  return vs a benchmark, plus holdings-based composition comparison.
--
--  Table: sec_alloc_perf_attribution
--    Combines two formerly separate analyses:
--      1. Daily return decomposition (subject/benchmark/active return +
--         Brinson-Fachler allocation_effect)
--      2. Composition correlation (shared weight overlap + volume ratio
--         vs benchmark)
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
--  RETURN DEFINITION:
--    return = today's close - previous trading day's close
--    (absolute price/points difference, NOT a fractional ratio.
--     Computed from raw close for stocks/indices; for ETFs uses
--     adj_close when available, else falls back to raw close.)
--
--  Per-row decomposition:
--    subject_return     = subject's daily return (close_t - close_{t-1})
--    benchmark_return   = benchmark's daily return (close_t - close_{t-1})
--    active_return      = subject_return - benchmark_return
--    allocation_effect  = Σ_i (w_subject,i - w_market,i) * r_stock,i
--      (Brinson-Fachler holdings attribution; NULL for stocks)
--
--  Composition correlation (NULL for stocks — no internal holdings):
--    code_sec_shared_weight      = Σ w_subject   on shared (overlapping) stocks
--    benchmark_sec_shared_weight = Σ w_benchmark on shared stocks
--
--  ETF-MARKET AMOUNT (replaces former raw index/ETF amount columns):
--    benchmark_etf_amount = Σ etf_liquidity_margin.amount_wan × 1e4 across
--                           ALL ETFs tracking benchmark_code on this date
--                           (parent_index_code = benchmark_code in
--                           stats.sec_classification). NULL when no ETF
--                           tracks the benchmark.
--    code_etf_amount      = subject's own amount (etf_liquidity_margin.amount_wan
--                           × 1e4) when sec_type='etf'; aggregate ETF amount
--                           tracking the subject index when sec_type='index'.
--    etf_amount_ratio_benchmark_to_code = GENERATED column =
--      benchmark_etf_amount / code_etf_amount (NULL when either is NULL/0).
--      A ratio ≥ 1 means the benchmark's ETF-market turnover is larger than
--      the subject's. The INVERSE (code_etf_amount / benchmark_etf_amount)
--      is the subject's SHARE of the benchmark's ETF market and is the
--      interpretable "proportion" form — computed in the UI as 1/ratio.
--
--  STATISTICAL ATTRIBUTION (rolling correlations):
--    corr_5d / corr_20d / corr_60d / corr_255d = rolling Pearson correlation
--      of subject close vs benchmark close over trailing N trading days.
--
--  Brinson-Fachler CORE concept (simplified):
--    The full formula subtracts r_b: Σ (w_p - w_b) * (r_stock - r_b),
--    but since Σ (w_p - w_b) = 0 when both sum to 1, the r_b term vanishes,
--    leaving Σ (w_p - w_b) * r_stock. Selection and interaction effects are
--    zero for ETF/index vs benchmark (same stocks, same returns).
--
--  REFACTOR NOTE: extending to richer holdings-based attribution (active_share,
--  cosine_similarity, overlap_weight, HHI, per-sector breakdowns, etc.) is a
--  straightforward `ALTER TABLE ... ADD COLUMN` — the PK and core columns
--  stay stable.
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.sec_alloc_perf_attribution
--  PK: (code, date, sec_type, benchmark_code)
--
--  Merges the former sec_composition_correlation table (composition overlap
--  & volume ratio) into the daily return decomposition table. The composition
--  columns (code_sec_shared_weight, benchmark_sec_shared_weight) are NULL for
--  stocks (no holdings).
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.sec_alloc_perf_attribution (
    code                    TEXT      NOT NULL,
    date                    DATE      NOT NULL,
    sec_type                TEXT      NOT NULL,  -- 'stock' | 'etf' | 'index'
    benchmark_code          TEXT      NOT NULL,

    subject_return          NUMERIC(10,6),  -- today close - prev close (subject)
    benchmark_return        NUMERIC(10,6),  -- today close - prev close (benchmark)
    active_return           NUMERIC(10,6),  -- subject_return - benchmark_return

    allocation_effect       NUMERIC(10,6),     -- Brinson-Fachler core: attribution by holdings.

    code_sec_shared_weight         NUMERIC(10,6),  -- Σ w_subject   on shared stocks
    benchmark_sec_shared_weight    NUMERIC(10,6),  -- Σ w_benchmark on shared stocks

    -- ETF-market turnover aggregated across ALL ETFs tracking the benchmark
    -- index (via stats.sec_classification.parent_index_code). NULL when no
    -- ETF tracks the benchmark (e.g. broad indices like 上证指数 000001).
    benchmark_etf_amount               NUMERIC(18,4),  -- Σ etf amount_wan×1e4 for ETFs tracking benchmark_code
    -- Subject's own ETF amount (sec_type='etf') OR aggregate ETF amount
    -- tracking the subject index (sec_type='index'). NULL for stocks.
    code_etf_amount                    NUMERIC(18,4),
    etf_amount_ratio_benchmark_to_code NUMERIC(20,4)
        GENERATED ALWAYS AS (
            CASE
                WHEN benchmark_etf_amount IS NULL OR code_etf_amount IS NULL
                  OR benchmark_etf_amount = 0 OR code_etf_amount = 0
                THEN NULL
                ELSE benchmark_etf_amount / code_etf_amount
            END
        ) STORED,

    corr_5d                NUMERIC(10,6),  -- 5-day close correlation between subject and benchmark
    corr_20d               NUMERIC(10,6),  -- 20-day close correlation between subject and benchmark
    corr_60d               NUMERIC(10,6),  -- 60-day close correlation between subject and benchmark
    corr_255d              NUMERIC(10,6),  -- 255-day close correlation between subject and benchmark

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

COMMENT ON TABLE  analysis.sec_alloc_perf_attribution                  IS 'Daily performance attribution + composition correlation: one row per (code, date, sec_type, benchmark_code). Decomposes subject_return into active_return (vs benchmark) and Brinson-Fachler allocation_effect (holdings-driven). Also stores composition overlap metrics (code_sec_shared_weight, benchmark_sec_shared_weight), ETF-market turnover (benchmark_etf_amount, code_etf_amount, etf_amount_ratio_benchmark_to_code), and rolling close correlations (corr_5d/20d/60d/255d). sec_type ∈ {stock, etf, index}. Composition and ETF-amount columns are NULL for stocks.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.sec_type         IS 'Subject security type: stock, etf, or index. Determines which source price table and (for etf/index) which composition source applies.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_code IS 'Benchmark index code (typically one of the 6 broad-market indices: 000300, 000001, 000852, 399001, 399006, 000688, but not constrained).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.subject_return   IS 'Subject''s daily return = today''s close - previous trading day''s close (absolute price/points difference). For ETFs uses adj_close when available.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_return  IS 'Benchmark''s daily return = today''s close - previous trading day''s close (absolute price/points difference).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.active_return    IS 'subject_return - benchmark_return. Difference of the two absolute daily returns.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.allocation_effect IS 'Brinson-Fachler allocation effect (core holdings attribution): Σ_i (w_subject,i - w_market,i) * r_stock,i. For ETF/index, weights come from stats.sec_composition. NULL for stocks (no internal holdings). Equals active_return when both weight vectors sum to 1 over the same stock universe.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.code_sec_shared_weight         IS 'Σ w_subject on stocks held by BOTH securities. The fraction of the subject''s weight that overlaps with the benchmark. NULL for stocks (no internal holdings).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_sec_shared_weight    IS 'Σ w_benchmark on stocks held by BOTH securities. The fraction of the benchmark''s weight that overlaps with the subject. NULL for stocks (no internal holdings).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_etf_amount           IS 'Aggregate ETF turnover (yuan) on this date across ALL ETFs tracking benchmark_code. Source: Σ stats.etf_liquidity_margin.amount_wan × 1e4 where the ETF''s stats.sec_classification.parent_index_code = benchmark_code. NULL when no ETF tracks the benchmark (e.g. 上证指数 000001 has no direct ETF tracking it).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.code_etf_amount                IS 'Subject''s ETF turnover (yuan). For sec_type=''etf'': the ETF''s own stats.etf_liquidity_margin.amount_wan × 1e4. For sec_type=''index'': aggregate ETF turnover tracking the subject index (same aggregation as benchmark_etf_amount but keyed on subject code). NULL for stocks and for indices with no tracking ETF.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.etf_amount_ratio_benchmark_to_code IS 'GENERATED ALWAYS AS (CASE WHEN benchmark_etf_amount IS NULL OR code_etf_amount IS NULL OR benchmark_etf_amount = 0 OR code_etf_amount = 0 THEN NULL ELSE benchmark_etf_amount / code_etf_amount END). Ratio ≥ 1 means benchmark''s ETF-market turnover exceeds subject''s. NOTE: this is a LIQUIDITY ratio, not a price-attribution proportion. The subject''s SHARE of the benchmark ETF market = 1 / etf_amount_ratio_benchmark_to_code (computed in UI).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.corr_5d                IS 'Rolling Pearson correlation of subject close vs benchmark close over trailing 5 trading days (min_periods ≈ 2N/3). NULL when insufficient non-NaN data.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.corr_20d               IS 'Rolling Pearson correlation of subject close vs benchmark close over trailing 20 trading days (min_periods ≈ 2N/3).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.corr_60d               IS 'Rolling Pearson correlation of subject close vs benchmark close over trailing 60 trading days (min_periods ≈ 2N/3).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.corr_255d              IS 'Rolling Pearson correlation of subject close vs benchmark close over trailing 255 trading days (min_periods ≈ 2N/3).';


-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('sec_alloc_perf_attribution', 'sec_alloc_perf_attribution', NULL, NOW(),
     'Unified daily performance attribution + composition correlation across stocks, ETFs, and sub-indices. Decomposes subject_return into active_return (vs any benchmark) and Brinson-Fachler allocation_effect (holdings-driven: Σ (w_subject - w_market) * r_stock). Also stores composition overlap metrics (code_sec_shared_weight, benchmark_sec_shared_weight), ETF-market turnover (benchmark_etf_amount = Σ across ETFs tracking the benchmark; code_etf_amount = subject''s own ETF amount or aggregate for index subjects; etf_amount_ratio_benchmark_to_code = GENERATED ratio), and rolling close correlations (corr_5d/20d/60d/255d). allocation_effect and composition columns are NULL for stocks.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
