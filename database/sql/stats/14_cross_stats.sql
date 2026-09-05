-- ============================================================================
--  Cross-Security Stats — THE canonical cross-security composition-overlap
--  and trading-amount share table. One table hosts BOTH grains of the
--  "share weights to benchmark" cross logic, unified by `sec_type`:
--
--    sec_type = 'index'    (code, benchmark_code) = index/index PAIR grain
--                          (former analysis.sec_alloc_perf_attribution,
--                          migrated 2026-09-04; that table becomes a VIEW
--                          over these rows after the switchover).
--    sec_type = 'industry' code = industry_id, benchmark_code = broad-market
--                          index. INDUSTRY grain (former broad-market half of
--                          analysis.industry_attributions — the weights +
--                          trading-amount split; the attribution-specific
--                          return/rolling-price decomposition stays in
--                          analysis.industry_attributions).
--    sec_type = 'etf'      reserved (pair grain with ETF subjects — the
--                          ETF subject pipeline is currently bypassed, same
--                          as the former sec_alloc_perf_attribution).
--
--  CROSS LOGIC HOSTED HERE (the primitive chain)
--    stats.sec_composition (LATEST snapshot per code, stock_code NOT NULL)
--      holdings: (code, stock_code, weight_pct)
--      → PAIR grain:   code_sec_shared_weight      = Σ w_code  on stocks
--                      held by BOTH code and benchmark_code
--                      benchmark_sec_shared_weight = Σ w_benchmark on the
--                      same shared stocks. Snapshot-constant, replicated
--                      per (code, benchmark_code, date) so consumers get
--                      one PK-grain time series.
--      → INDUSTRY grain: benchmark_sec_shared_weight = Σ w_benchmark over
--                      the UNION of stocks held by ANY industry member
--                      (each stock counted ONCE — union, not sum-per-index);
--                      code_sec_shared_weight = SUM of member indices'
--                      own shared weights vs the benchmark (the former
--                      'trading_amt' industry_shared_weight; can exceed
--                      100 — sum of multiple member portfolios, NOT
--                      double-counting). The former 'equal' variant
--                      (= SUM / member_count) is DERIVABLE at read time
--                      and not materialized here.
--
--    TRADING-AMOUNT SHARE SPLIT (industry grain; pair-grain rows leave
--    these NULL — the per-pair stock-universe turnover is not consumed
--    anywhere and would explode the compute):
--      benchmark_trading_amount  = stats.index_basic_stats.trading_amount
--                                  for the benchmark (yuan).
--      shared_trading_amount     = Σ stock_liquidity_margin.trading_amount
--                                  over the industry-union ∩ benchmark
--                                  shared stocks on that date (a stock
--                                  contributes only when it has a non-NULL
--                                  close that date — parity with the former
--                                  attributions computation).
--      non_shared_trading_amount = benchmark − shared (NULL when either is
--                                  NULL) — the former
--                                  benchmark_non_this_industry_trading_amt.
--
--    ETF-MARKET LIQUIDITY (pair grain only; from stats.index_exts
--    total_etf_trading_amount keyed on the tracked index code):
--      benchmark_etf_trading_amount / code_etf_trading_amount (yuan),
--      etf_trading_amount_ratio_benchmark_to_code = bench/code (NULL when
--      either is NULL/0 or |ratio| >= 1e6 — the NUMERIC(10,4) cap),
--      +_ma5 = 5-trading-day rolling mean per (code, benchmark_code),
--      min_periods=1.
--
--    ROLLING CORRELATIONS (pair grain only): corr_20d/60d/255d Pearson
--    corr of close prices, materialized ONLY on stride-20 grid dates of
--    the global index calendar (non-grid dates NULL) — mirrors
--    analysis.industry_correlations. Written by the `--corr` sub-command
--    (off by default in the main run).
--
--  TEMPORAL CONVENTION (unchanged from the migrated tables)
--    LATEST sec_composition snapshot per code for ALL dates (temporal
--    extrapolation — sec_composition only has recent snapshots). The
--    daily-varying inputs are the ETF amounts, trading amounts and corrs;
--    the shared weights are snapshot-constant.
--
--  BULK-COPY FRIENDLINESS (project convention)
--    • Plain columns only — no GENERATED ALWAYS, no per-row CHECK
--      constraints (the loader guarantees code formats + enums; per-row
--      validation costs more on bulk COPY than it protects).
--    • No secondary index in the DDL — the (sec_type, date) index is
--      POST-CREATED by the pipeline after the bulk COPY (a live-maintained
--      index during a multi-million-row load costs far more than one
--      rebuild at the end).
--    • Hash partitions (8) keyed by `code` — the leading PK key, so a
--      subject's rows never split across partitions and force-mode
--      COPY streams key-major.
--    • Dates map table (stats.cross_stats_dates) for O(1) missing-date
--      detection — date-only scans on the main table are expensive
--      (code is the HASH key).
--
--  POPULATION
--    builds.cross_stats (python -m builds.cross_stats): incremental
--    (missing dates only, source stats.index_identity) / --force
--    (truncate-then-recompute) / --corr (corr-only upsert on grid dates).
--    Requires stats.sec_composition (source_type='index') to be populated
--    first — the runner exits(1) with instructions when it is empty
--    (run `python -m builds.index` phase 1). Also requires
--    stats.index_exts (builds.index phase 3) for the ETF amounts.
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.cross_stats (
    code              TEXT   NOT NULL,  -- index code, or industry_id when sec_type='industry'
    benchmark_code    TEXT   NOT NULL,  -- benchmark index code
    date              DATE   NOT NULL,
    sec_type          TEXT   NOT NULL,  -- 'index' | 'etf' | 'industry'

    -- Composition share weights (percent 0-100; from LATEST sec_composition)
    -- code_sec_shared_weight: for pair grain Σ w_code on shared stocks;
    --   for industry grain SUM of member indices' pair shared weights
    --   (former 'trading_amt' industry_shared_weight; can exceed 100).
    code_sec_shared_weight        NUMERIC(8,4),
    benchmark_sec_shared_weight   NUMERIC(8,4),  -- Σ w_benchmark on shared stocks (pair) / industry-union stocks (industry)

    -- Trading-amount share split (yuan; industry-grain rows only)
    benchmark_trading_amount      NUMERIC(24,4),
    shared_trading_amount         NUMERIC(24,4),
    non_shared_trading_amount     NUMERIC(24,4),  -- benchmark − shared (NULL when either NULL)

    -- ETF-market liquidity (pair grain only; from stats.index_exts)
    benchmark_etf_trading_amount               NUMERIC(16,2),
    code_etf_trading_amount                    NUMERIC(16,2),
    etf_trading_amount_ratio_benchmark_to_code     NUMERIC(10,4),
    etf_trading_amount_ratio_benchmark_to_code_ma5 NUMERIC(10,4),

    -- Rolling close correlations (pair grain, stride-20 grid dates only)
    corr_20d          NUMERIC(8,4),
    corr_60d          NUMERIC(8,4),
    corr_255d         NUMERIC(8,4),

    CONSTRAINT pk_cross_stats PRIMARY KEY (code, benchmark_code, date, sec_type)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'cross_stats', 8);

-- ----------------------------------------------------------------------------
--  Dates map: one row per date loaded into cross_stats (pair grain drives
--  the map; industry grain shares the same dates by construction). Missing-
--  date detection queries this tiny map instead of scanning the main table.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stats.cross_stats_dates (
    date DATE PRIMARY KEY
);

-- NO secondary index in the DDL — POST-CREATED by the pipeline after the
-- bulk COPY: idx_cross_stats_sec_type_date ON (sec_type, date).

COMMENT ON TABLE  stats.cross_stats IS 'Cross-security composition-overlap + trading-amount share stats. sec_type=''index'': (code, benchmark_code) index pair grain (former analysis.sec_alloc_perf_attribution — shared weights, ETF-market liquidity, stride-20 grid correlations). sec_type=''industry'': code=industry_id vs broad-market benchmark (former broad-market half of analysis.industry_attributions — union-overlap weights + benchmark/shared/non-shared trading-amount split). sec_type=''etf'' reserved. Weights from LATEST sec_composition snapshot for all dates (temporal extrapolation). Built by builds.cross_stats (incremental / --force / --corr); requires builds.index composition (phase 1) + index_exts (phase 3) first.';
COMMENT ON COLUMN stats.cross_stats.code IS 'Subject: index code (pair grain) or industry_id (sec_type=''industry'').';
COMMENT ON COLUMN stats.cross_stats.benchmark_code IS 'Benchmark index code. Industry grain is computed only for broad-market benchmarks (stats.sec_index_tags.is_broad_market).';
COMMENT ON COLUMN stats.cross_stats.sec_type IS 'Grain discriminator: ''index'' (pair), ''industry'' (industry union vs broad benchmark), ''etf'' (reserved).';
COMMENT ON COLUMN stats.cross_stats.code_sec_shared_weight IS 'Pair grain: SUM w_code on stocks held by BOTH code and benchmark (percent). Industry grain: SUM of member indices'' pair shared weights vs the benchmark (former trading_amt industry_shared_weight; can exceed 100 — sum of member portfolios, not double-counting).';
COMMENT ON COLUMN stats.cross_stats.benchmark_sec_shared_weight IS 'Pair grain: SUM w_benchmark on the same shared stocks. Industry grain: benchmark''s weight on the UNION of industry member stocks (each stock counted once; bounded [0,100]; recomputed from compositions to avoid member double-counting).';
COMMENT ON COLUMN stats.cross_stats.benchmark_trading_amount IS 'Benchmark index trading_amount (yuan, stats.index_basic_stats). Industry-grain rows only; NULL on pair-grain rows.';
COMMENT ON COLUMN stats.cross_stats.shared_trading_amount IS 'SUM stock_liquidity_margin.trading_amount over the industry-union ∩ benchmark shared stocks on this date (stock contributes only with a non-NULL close that date — parity with the former attributions computation). Industry-grain rows only.';
COMMENT ON COLUMN stats.cross_stats.non_shared_trading_amount IS 'benchmark_trading_amount − shared_trading_amount (NULL when either is NULL). The former benchmark_non_this_industry_trading_amt. Industry-grain rows only.';
COMMENT ON COLUMN stats.cross_stats.benchmark_etf_trading_amount IS 'Aggregate ETF turnover tracking benchmark_code (stats.index_exts.total_etf_trading_amount; NULL when no ETF tracks the benchmark). Pair-grain rows only.';
COMMENT ON COLUMN stats.cross_stats.code_etf_trading_amount IS 'Aggregate ETF turnover tracking the subject index (pair grain, sec_type=''index'').';
COMMENT ON COLUMN stats.cross_stats.etf_trading_amount_ratio_benchmark_to_code IS 'benchmark_etf_trading_amount / code_etf_trading_amount (NULL when either NULL/0 or |ratio| >= 1e6 — NUMERIC(10,4) cap). Its INVERSE is the subject''s share of the benchmark ETF market (UI computes 1/ratio). Liquidity ratio, not price attribution.';
COMMENT ON COLUMN stats.cross_stats.etf_trading_amount_ratio_benchmark_to_code_ma5 IS '5-trading-day rolling mean of the ratio per (code, benchmark_code), min_periods=1.';
COMMENT ON COLUMN stats.cross_stats.corr_20d IS 'Trailing 20-trading-day Pearson corr of close prices (stride-20 grid dates only; NULL elsewhere).';
COMMENT ON COLUMN stats.cross_stats.corr_60d IS 'Trailing 60-trading-day Pearson corr of close prices (stride-20 grid dates only).';
COMMENT ON COLUMN stats.cross_stats.corr_255d IS 'Trailing 255-trading-day Pearson corr of close prices (stride-20 grid dates only).';
COMMENT ON TABLE  stats.cross_stats_dates IS 'One row per date loaded into stats.cross_stats (pair grain). Missing-date detection reads this map instead of scanning the hash-partitioned main table. Maintained by builds.cross_stats.';
