-- ============================================================================
--  Intraday Industry Sentiments — per-5-min-tick % change vs previous trading
--  day's close, decomposed to the industry and individual-index level.
--
--  Two tables (parent → child strict composite FK). PK uses (date, time) as
--  two separate columns to align with stats.*_intraday_5min tables.
--
--  analysis.intraday_industry_market_movements   (PARENT — industry aggregate)
--    PK: (industry_id, date, time, benchmark_code)
--      date   DATE       = intraday 5-min bar date
--      time   TIME       = intraday 5-min bar time (09:30, 09:35, ... 15:00)
--      benchmark_code     = the benchmark this industry's aggregation is
--                           attributed to (one industry can be attributed to
--                           multiple benchmarks; hence benchmark_code is in
--                           the PK)
--      is_industry_not_strategy  BOOLEAN  denormalized from sec_classification
--                           so the UI can color-code without an extra JOIN
--    benchmark_price_pct_relative_prev_date_close
--                         = benchmark.close / prev_day_close - 1
--                           (prev_day_close from stats.index_basic_stats at
--                           the latest date < tick date with non-NULL close)
--    industry_price_pct_relative_prev_date_close
--                         = mean of member indices' code_price_pct across
--                           this industry for this benchmark at this tick
--
--  analysis.intraday_index_market_movements      (CHILD — individual index)
--    PK: (code, date, time, sec_type, benchmark_code)
--      sec_type TEXT ∈ ('index' for now; 'stock' / 'etf' reserved)
--      industry_id + date + time + benchmark_code form the composite FK to
--      the parent
--    FK: (industry_id, date, time, benchmark_code)
--        → intraday_industry_market_movements(industry_id, date, time, benchmark_code)
--    code_price_pct_relative_prev_date_close
--                         = member_index.close / prev_day_close - 1
--
--  NOTE on benchmark_code duplication in the child table:
--    The same (code, date, time, sec_type) row's code_price_pct is
--    mathematically identical regardless of which benchmark it's being
--    attributed to — the index's price move doesn't depend on the
--    benchmark. We nonetheless store one child row per benchmark so the
--    strict composite FK to the parent
--    (industry_id, date, time, benchmark_code) is satisfiable. The
--    duplication cost is one TEXT column per row — small relative to the
--    FLOAT data.
--
--  Populated by analyze.intraday_industry_sentiments (Python module).
--  Per project rule, all INSERTs are in Python (no raw INSERT...SELECT SQL).
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.intraday_industry_market_movements (
    industry_id                                TEXT      NOT NULL,
    date                                       DATE      NOT NULL,
    time                                       TIME      NOT NULL,
    benchmark_code                             TEXT      NOT NULL,
    is_industry_not_strategy                   BOOLEAN   NOT NULL,
    benchmark_price_pct_relative_prev_date_close FLOAT,
    industry_price_pct_relative_prev_date_close  FLOAT,
    -- industry_price_pct_relative_prev_date_close - benchmark_price_pct_relative_prev_date_close.
    -- Computed in Python (per project rule: ad-hoc SQL insert/update ops are
    -- consolidated in Python code). Stored so the UI shade can use the diff
    -- directly without recomputing on every render.
    industry_price_pct_vs_benchmark_price_pct  FLOAT,
    PRIMARY KEY (industry_id, date, time, benchmark_code)
);

-- Idempotent migration for existing DBs that already have the table from a
-- prior schema version (without this column). ADD COLUMN IF NOT EXISTS is
-- a no-op on fresh installs that just created the table above.
ALTER TABLE analysis.intraday_industry_market_movements
    ADD COLUMN IF NOT EXISTS industry_price_pct_vs_benchmark_price_pct FLOAT;

CREATE TABLE IF NOT EXISTS analysis.intraday_index_market_movements (
    code                                     TEXT      NOT NULL,
    date                                      DATE      NOT NULL,
    time                                      TIME      NOT NULL,
    sec_type                                  TEXT      NOT NULL,  -- 'index' for now
    industry_id                               TEXT      NOT NULL,
    benchmark_code                            TEXT      NOT NULL,
    code_price_pct_relative_prev_date_close   FLOAT,
    PRIMARY KEY (code, date, time, sec_type, benchmark_code),
    FOREIGN KEY (industry_id, date, time, benchmark_code)
        REFERENCES analysis.intraday_industry_market_movements
        (industry_id, date, time, benchmark_code)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_intraday_ind_mm_bench_dt
    ON analysis.intraday_industry_market_movements (benchmark_code, date, time);

CREATE INDEX IF NOT EXISTS idx_intraday_ind_mm_ind_bench
    ON analysis.intraday_industry_market_movements (industry_id, benchmark_code);

CREATE INDEX IF NOT EXISTS idx_intraday_idx_mm_bench_dt
    ON analysis.intraday_index_market_movements (benchmark_code, date, time);

CREATE INDEX IF NOT EXISTS idx_intraday_idx_mm_ind_bench_dt
    ON analysis.intraday_index_market_movements (industry_id, benchmark_code, date, time);
