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
--    amount_ratio_benchmark_to_code = benchmark_amount / code_amount
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
--  columns (code_sec_shared_weight, benchmark_sec_shared_weight,
--  amount_ratio_benchmark_to_code) are NULL for stocks (no holdings).
-- ----------------------------------------------------------------------------
CREATE TABLE analysis.sec_alloc_perf_attribution (
    code                    TEXT      NOT NULL,
    date                    DATE      NOT NULL,
    code_sec_type                TEXT      NOT NULL,  -- 'stock' | 'etf' | 'index'
    benchmark_code          TEXT      NOT NULL,
    benchmark_sec_type                TEXT      NOT NULL,  -- 'stock' | 'etf' | 'index'

    subject_return          NUMERIC(10,6),  -- today close - prev close (subject)
    benchmark_return        NUMERIC(10,6),  -- today close - prev close (benchmark)
    active_return           NUMERIC(10,6),  -- subject_return - benchmark_return

    allocation_effect       NUMERIC(10,6),     -- Brinson-Fachler core: attribution by holdings.

    code_sec_shared_weight         NUMERIC(10,6),  -- Σ w_subject   on shared stocks
    benchmark_sec_shared_weight    NUMERIC(10,6),  -- Σ w_benchmark on shared stocks

    benchmark_amount               NUMERIC(18,4),  -- benchmark amount (yuan; src=index_basic_stats.amount×1e8)
    code_amount                    NUMERIC(18,4),  -- subject code amount (yuan; src=etf_liquidity_margin.amount_wan×1e4)
    amount_ratio_benchmark_to_code NUMERIC(20,4)
        GENERATED ALWAYS AS (
            CASE
                WHEN benchmark_amount IS NULL OR code_amount IS NULL
                  OR benchmark_amount = 0 OR code_amount = 0
                THEN NULL
                ELSE benchmark_amount / code_amount
            END
        ) STORED,

    CONSTRAINT pk_sec_alloc_perf_attribution
        PRIMARY KEY (code, date, code_sec_type, benchmark_code),
    CONSTRAINT chk_code_sec_perf_attr_sec_type
        CHECK (code_sec_type IN ('stock', 'etf', 'index')),
    CONSTRAINT chk_code_sec_perf_attr_code_format CHECK (
        (code_sec_type = 'stock' AND code ~ '^\d{6}\.(SZ|SS|BJ)$')
        OR (code_sec_type = 'etf'   AND code ~ '^\d{6}\.(SZ|SS|SH)$')
        OR (code_sec_type = 'index' AND code ~ '^(\d{6}|H\d{5})$')
    )
);

CREATE INDEX idx_sec_perf_attr_date_code_benchmark
    ON analysis.sec_alloc_perf_attribution (date, code, benchmark_code);
CREATE INDEX idx_sec_perf_attr_sec_type_date
    ON analysis.sec_alloc_perf_attribution (sec_type, date);

COMMENT ON TABLE  analysis.sec_alloc_perf_attribution                  IS 'Daily performance attribution + composition correlation: one row per (code, date, sec_type, benchmark_code). Decomposes subject_return into active_return (vs benchmark) and Brinson-Fachler allocation_effect (holdings-driven). Also stores composition overlap metrics (code_sec_shared_weight, benchmark_sec_shared_weight, amount_ratio_benchmark_to_code). sec_type ∈ {stock, etf, index}. Composition columns are NULL for stocks.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.sec_type         IS 'Subject security type: stock, etf, or index. Determines which source price table and (for etf/index) which composition source applies.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_code IS 'Benchmark index code (typically one of the 6 broad-market indices: 000300, 000001, 000852, 399001, 399006, 000688, but not constrained).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.subject_return   IS 'Subject''s daily return = today''s close - previous trading day''s close (absolute price/points difference). For ETFs uses adj_close when available.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_return  IS 'Benchmark''s daily return = today''s close - previous trading day''s close (absolute price/points difference).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.active_return    IS 'subject_return - benchmark_return. Difference of the two absolute daily returns.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.allocation_effect IS 'Brinson-Fachler allocation effect (core holdings attribution): Σ_i (w_subject,i - w_market,i) * r_stock,i. For ETF/index, weights come from stats.sec_composition. NULL for stocks (no internal holdings). Equals active_return when both weight vectors sum to 1 over the same stock universe.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.code_sec_shared_weight         IS 'Σ w_subject on stocks held by BOTH securities. The fraction of the subject''s weight that overlaps with the benchmark. NULL for stocks (no internal holdings).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_sec_shared_weight    IS 'Σ w_benchmark on stocks held by BOTH securities. The fraction of the benchmark''s weight that overlaps with the subject. NULL for stocks (no internal holdings).';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.benchmark_amount               IS 'Benchmark''s amount (成交金额) on this date in yuan. Sourced from stats.index_basic_stats.amount × 1e8 (src unit: 亿元). NULL when source amount is NULL.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.code_amount                    IS 'Subject code''s amount (成交金额) on this date in yuan. For ETFs: stats.etf_liquidity_margin.amount_wan × 1e4 (src unit: 万元). NULL when no liquidity_margin row.';
COMMENT ON COLUMN analysis.sec_alloc_perf_attribution.amount_ratio_benchmark_to_code IS 'GENERATED ALWAYS AS (CASE WHEN benchmark_amount IS NULL OR code_amount IS NULL OR benchmark_amount = 0 OR code_amount = 0 THEN NULL ELSE benchmark_amount / code_amount END). Amount ratio: how many times the benchmark''s yuan amount vs the subject''s. Automatically computed — cannot be inserted or updated directly. NULL when either amount is NULL or zero.';


-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('sec_alloc_perf_attribution', 'sec_alloc_perf_attribution', NULL, NOW(),
     'Unified daily performance attribution + composition correlation across stocks, ETFs, and sub-indices. Decomposes subject_return into active_return (vs any benchmark) and Brinson-Fachler allocation_effect (holdings-driven: Σ (w_subject - w_market) * r_stock). Also stores composition overlap metrics (code_sec_shared_weight, benchmark_sec_shared_weight, amount_ratio_benchmark_to_code). allocation_effect and composition columns are NULL for stocks.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
