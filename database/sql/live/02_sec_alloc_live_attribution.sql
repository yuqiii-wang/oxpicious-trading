-- ============================================================================
--  LIVE Allocation Attribution — per-5-min-tick member attribution weighted
--  by the PREVIOUS TRADING DAY's trading amount (liquidity weight).
--
--  Two tables, split by computation weight:
--
--  1) live.sec_alloc_live_prev_ref   (STATIC REFERENCE — heavy, once per date)
--     One row per (benchmark_code, date, code, sec_type). All "*prev_date*"
--     reference values (prev-day close, prev-day trading amount, normalized
--     trading-amount market-share weight) are computed ONCE per (benchmark,
--     date) from PREVIOUS trading day data only. The tick table never
--     recomputes these.
--     Member universe: ALL active classified indices (with non-BROAD
--     industry_id), regardless of composition overlap. Shared weights
--     come from stats.sec_composition directly (computed in _REF_MEMBERS_SQL).
--     Zero-overlap members (code_sec_shared_weight = 0) still appear in the
--     equal-weight aggregate but contribute zero to the trading-amount
--     weighted aggregate. STOCKS are kept here for SHARE WEIGHT purposes
--     only — they carry no live tick rows.
--
--  2) live.sec_alloc_live_attribution (LIVE — light, per 5-min tick)
--     One row per (code, date, time, sec_type, benchmark_code). Only
--     per-tick values: member tick %, benchmark tick % (denormalized),
--     GENERATED diff.
--     NO FK to the ref table: FALLBACK rows (is_without_trading_amt =
--     TRUE) are written when the ref for (benchmark, date) is not ready
--     yet (heavy pass still running / prev-day basic_stats lagging), and
--     such rows have no ref parent. Once the ref is built, a later run
--     UPGRADES those rows in place to is_without_trading_amt = FALSE
--     (PK upsert — no duplicates).
--     LIVE TICK SCOPE: only members classified as 'index' or 'etf'
--     ('industry' members are indexes with an industry_id). Stocks never
--     get tick rows.
--
--  FALLBACK semantics (is_without_trading_amt = TRUE rows):
--    prev-day close basis = the member's LAST 5-min bar close of its
--    latest intraday date BEFORE the live date (self-contained in
--    stats.index_intraday_5min — no basic_stats dependency). Equal-
--    weighted aggregation only (no trading-amount weights available);
--    the UI disables the "by trading amt" toggle while no weighted rows
--    exist for the benchmark+date.
--
--  CONCURRENCY: python -m live.sec_alloc_live_attribution takes a PG
--  advisory lock. If another instance is detected running, the new
--  instance runs the FALLBACK-ONLY pass (no heavy ref build, no
--  weighted upgrades) so live data keeps flowing without duplication.
--
--  Weight semantics (in the REF table):
--    code_prev_date_trading_amount = member trading amount (yuan) from
--      the latest stats.index_basic_stats row < date with a REAL
--      (non-NULL, non-NaN) trading_amount within a 14-day lookback.
--    code_trading_amount_weight    = member amount / Σ member amounts
--      over the benchmark's eligible universe (renormalized over
--      members with a real amount). 0..1, Σ = 1 per (benchmark, date).
--
--  UI TOGGLE — "by trading amt" vs "without trading amt":
--    Industry-level aggregates are computed AT QUERY TIME from the tick
--    table (no precomputed industry parent):
--      • weighted : SUM(ref.code_trading_amount_weight *
--                       ref.code_sec_shared_weight *
--                       tick.code_price_pct_relative_prev_date_close)
--                   / SUM(ref.code_trading_amount_weight *
--                        ref.code_sec_shared_weight)  -- renormalized
--                      over members with non-NULL pct (stocks hold
--                      weights but no ticks, so they cancel out).
--                      code_sec_shared_weight = 0 ZEROes out disjoint
--                      indices (no composition overlap with benchmark).
--      • equal    : AVG(tick.code_price_pct_relative_prev_date_close)
--
--  All pct columns are FRACTIONS (0.01 = 1%), matching the intraday_*
--  market-movements tables and the UI (×100 at render time).
--
--  Populated by Python (per project rule: INSERTs live in Python code, not
--  raw INSERT...SELECT SQL).
-- ============================================================================

-- ----------------------------------------------------------------------------
--  1) STATIC REFERENCE — prev-date heavy computation, once per (benchmark,
--     date). Parent of the tick table.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS live.sec_alloc_live_prev_ref (
    benchmark_code           TEXT      NOT NULL,
    date                     DATE      NOT NULL,   -- the LIVE date this ref serves
    code                     TEXT      NOT NULL,   -- member index code
    sec_type                 TEXT      NOT NULL DEFAULT 'index',  -- 'index' for now; 'stock'/'etf' reserved
    industry_id              TEXT      NOT NULL,   -- denormalized for drill-down GROUP BY
    is_industry_not_strategy BOOLEAN   NOT NULL,   -- denormalized for UI industry/strategy toggle

    -- Reference previous trading day (ALL "*prev_date*" columns are as-of
    -- this date). Stored explicitly so the liquidity-weight semantics are
    -- unambiguous and debuggable without re-deriving the calendar.
    prev_date                DATE      NOT NULL,

    -- ---- raw prev-date inputs (transparency + recomputation) --------------
    code_prev_date_close          NUMERIC(16,4),  -- member close on prev_date
    benchmark_prev_date_close     NUMERIC(16,4),  -- benchmark close on prev_date (same for all members of the benchmark)
    code_prev_date_trading_amount NUMERIC(24,4),  -- member trading amount on prev_date (yuan)

    -- ---- derived ONCE per (benchmark, date) --------------------------------
    -- Member share of Σ prev-day trading amounts across the benchmark's
    -- eligible member universe (renormalized over non-NULL amounts). 0..1.
    code_trading_amount_weight    NUMERIC(10,8),

    -- Composition overlap: member's share of benchmark's composition.
    -- Sourced from analysis.sec_alloc_perf_attribution (latest date).
    -- 0.0 = disjoint (no overlapping stocks); NULL = no composition data.
    -- Used by the "By Trading Amt" weighted aggregate to zero-out members
    -- with zero composition overlap (their pct moves don't reflect
    -- benchmark composition changes).
    code_sec_shared_weight        NUMERIC(12,8),

    CONSTRAINT pk_sec_alloc_live_prev_ref
        PRIMARY KEY (benchmark_code, date, code, sec_type),
    CONSTRAINT chk_sec_alloc_live_prev_ref_sec_type
        CHECK (sec_type IN ('stock', 'etf', 'index'))
);

CREATE INDEX IF NOT EXISTS idx_sec_alloc_live_prev_ref_ind_bench_dt
    ON live.sec_alloc_live_prev_ref (industry_id, benchmark_code, date);
CREATE INDEX IF NOT EXISTS idx_sec_alloc_live_prev_ref_date
    ON live.sec_alloc_live_prev_ref (date);

-- ----------------------------------------------------------------------------
--  2) LIVE TICKS — light per-5-min values. NO FK to the ref table
--     (fallback rows have no ref parent; consistency is enforced by the
--     pipeline, which upgrades fallback rows in place once the ref exists).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS live.sec_alloc_live_attribution (
    code                     TEXT      NOT NULL,
    date                     DATE      NOT NULL,   -- intraday 5-min bar date
    time                     TIME      NOT NULL,   -- intraday 5-min bar time
    sec_type                 TEXT      NOT NULL DEFAULT 'index',
    benchmark_code           TEXT      NOT NULL,
    is_without_trading_amt   BOOLEAN   NOT NULL,   -- TRUE = FALLBACK row (no ref yet, equal-weight only); FALSE = ref-based row (weighted-capable)
    is_without_benchmark     BOOLEAN   NOT NULL,   -- denormalized for UI by benchmark toggle


    -- % change vs prev-day close at this tick (FRACTIONS).
    code_price_pct_relative_prev_date_close       FLOAT,  -- member tick close / prev close - 1
    benchmark_price_pct_relative_prev_date_close  FLOAT,  -- benchmark tick close / prev close - 1

    -- member pct - benchmark pct (auto-maintained; NULL when either side NULL)
    code_price_pct_vs_benchmark_price_pct         FLOAT
        GENERATED ALWAYS AS (
            code_price_pct_relative_prev_date_close
          - benchmark_price_pct_relative_prev_date_close
        ) STORED,

    CONSTRAINT pk_sec_alloc_live_attribution
        PRIMARY KEY (code, date, time, sec_type, benchmark_code),
    CONSTRAINT chk_sec_alloc_live_sec_type
        CHECK (sec_type IN ('stock', 'etf', 'index'))
);

CREATE INDEX IF NOT EXISTS idx_sec_alloc_live_attr_bench_dt
    ON live.sec_alloc_live_attribution (benchmark_code, date, time);
CREATE INDEX IF NOT EXISTS idx_sec_alloc_live_attr_ind_bench_dt
    ON live.sec_alloc_live_attribution (benchmark_code, date, time, code);

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE  live.sec_alloc_live_prev_ref IS 'Static per-date reference for live attribution: prev trading day closes, prev-day trading amounts, and the normalized trading-amount market-share weight (code_trading_amount_weight, Σ = 1 per benchmark+date). One row per (benchmark_code, date, code, sec_type). Heavy prev-date computation happens ONCE here; the tick table (live.sec_alloc_live_attribution) never recomputes it. Member universe mirrors analysis.sec_alloc_perf_attribution (latest snapshot, code_sec_shared_weight > 0, active classification, non-BROAD industry).';
COMMENT ON COLUMN live.sec_alloc_live_prev_ref.prev_date IS 'The reference previous trading day all prev-date columns are computed against: latest date < the live date. Stored explicitly to make the liquidity-weight semantics unambiguous.';
COMMENT ON COLUMN live.sec_alloc_live_prev_ref.code_prev_date_trading_amount IS 'Member''s trading amount (yuan) on prev_date, from stats.index_basic_stats.trading_amount. The RAW liquidity weight source; NULL members are excluded from the weighted (renormalized) aggregate.';
COMMENT ON COLUMN live.sec_alloc_live_prev_ref.code_trading_amount_weight IS 'Member share of Σ prev-day trading amounts across the benchmark''s eligible member universe (0..1, Σ = 1 per benchmark+date after renormalizing over non-NULL amounts). Constant across all ticks of one date. Computed ONCE here (heavy); joined by the tick table for weighted aggregation.';

COMMENT ON TABLE  live.sec_alloc_live_attribution IS 'Live per-5-min-tick member attribution (light values only). One row per (code, date, time, sec_type, benchmark_code); NO FK to the ref table because FALLBACK rows (is_without_trading_amt = TRUE, written when the prev-date ref is not ready) have no ref parent — a later run upgrades them in place to FALSE once the ref exists. Tick rows exist ONLY for members classified as index/etf (industry members are indexes with an industry_id); stocks appear only in the ref table (share weights). Stores member + benchmark % vs prev-day close at each tick and the GENERATED diff. Industry-level weighted (SUM weight*pct, renormalized over members with ticks) and equal-weighted (AVG pct) aggregates are computed at QUERY TIME — no precomputed industry parent table. All pct columns are fractions (0.01 = 1%).';
COMMENT ON COLUMN live.sec_alloc_live_attribution.is_without_trading_amt IS 'TRUE = FALLBACK row computed WITHOUT the ref (prev close basis = member''s last 5-min bar close of the latest intraday date before the live date; equal-weighted aggregation only — the UI "by trading amt" toggle is disabled while only TRUE rows exist for the benchmark+date). FALSE = ref-based row (weighted-capable via sec_alloc_live_prev_ref.code_trading_amount_weight).';
COMMENT ON COLUMN live.sec_alloc_live_attribution.is_without_benchmark IS 'Denormalized toggle column. FALSE = row includes benchmark comparison (pct vs benchmark). TRUE = member-only pct without benchmark comparison.';
COMMENT ON COLUMN live.sec_alloc_live_attribution.code_price_pct_relative_prev_date_close IS 'Member intraday % change vs prev-day close at this tick (FRACTION): stats.index_intraday_5min.close / prev close - 1. Prev close basis: ref.code_prev_date_close (weighted rows) or the member''s prev-day last 5-min bar close (fallback rows).';
COMMENT ON COLUMN live.sec_alloc_live_attribution.benchmark_price_pct_relative_prev_date_close IS 'Benchmark intraday % change vs prev-day close at this tick (FRACTION), denormalized so attribution queries need no JOIN to the benchmark series.';
COMMENT ON COLUMN live.sec_alloc_live_attribution.code_price_pct_vs_benchmark_price_pct IS 'GENERATED: member pct - benchmark pct. Positive = member outperforming the benchmark at this tick.';

-- ----------------------------------------------------------------------------
--  Idempotent migrations (safe to re-run on fresh or upgraded databases):
--    • ensure toggle columns exist on any pre-existing table
--    • DROP the old strict FK — fallback rows (is_without_trading_amt =
--      TRUE) have no ref parent by design
-- ----------------------------------------------------------------------------
ALTER TABLE live.sec_alloc_live_attribution
    ADD COLUMN IF NOT EXISTS is_without_trading_amt BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_without_benchmark     BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE live.sec_alloc_live_attribution
    DROP CONSTRAINT IF EXISTS fk_sec_alloc_live_attr_ref;

ALTER TABLE live.sec_alloc_live_prev_ref
    ADD COLUMN IF NOT EXISTS code_sec_shared_weight NUMERIC(12,8);

COMMENT ON COLUMN live.sec_alloc_live_prev_ref.code_sec_shared_weight IS 'Member composition overlap weight vs benchmark (SUM of member weight_pct on overlapping stocks from stats.sec_composition). 0.0 = disjoint indices (no overlapping stocks); column is populated for ALL classified members. Multiplied with code_trading_amount_weight in the "By Trading Amt" weighted aggregate so zero-overlap members contribute zero to the benchmark attribution.';

-- ----------------------------------------------------------------------------
--  Register in live.live_identity
-- ----------------------------------------------------------------------------
INSERT INTO live.live_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('sec_alloc_live_attribution', 'sec_alloc_live_attribution', 'sec_alloc_live_prev_ref', NOW(),
     'Live per-5-min-tick member attribution under the live schema, split into two tables: live.sec_alloc_live_prev_ref (static per-date reference — prev-day closes, prev-day trading amounts, normalized trading-amount market-share weights computed ONCE from prev-date data; stocks kept for share weights only) and live.sec_alloc_live_attribution (light per-tick member + benchmark % vs prev-day close with GENERATED diff; tick rows only for index/etf members; NO FK — fallback rows is_without_trading_amt=TRUE are written when the ref is not ready, prev close basis = prev-day last 5-min bar close, and upgraded in place to FALSE once the ref exists). A PG advisory lock makes a second concurrent instance run the fallback-only pass. Industry-level weighted (SUM weight*pct, renormalized) and equal-weighted (AVG pct) aggregates are computed at query time. Sources: analysis.sec_alloc_perf_attribution (member universe), stats.index_basic_stats (prev-day close + trading_amount), stats.index_intraday_5min (tick closes + fallback prev closes), stats.sec_classification (industry tags).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
