-- ============================================================================
--  LIVE Allocation Attribution — per-5-min-tick member attribution weighted
--  by the PREVIOUS TRADING DAY's trading amount (liquidity weight).
--
--  ONE light table (the former companion live.sec_alloc_live_prev_ref
--  was CONSOLIDATED AWAY on 2026-09-08 — its prev-day values are
--  derivable from base tables and are computed at USE time):
--
--  live.sec_alloc_live_attribution (LIVE — light, per 5-min tick)
--     One row per (code, date, time, sec_type, benchmark_code). Only
--     per-tick values: member tick %, benchmark tick % (denormalized),
--     GENERATED diff.
--     LIVE TICK SCOPE: only members classified as 'index' or 'etf'
--     ('industry' members are indexes with an industry_id). Stocks never
--     get tick rows.
--
--  Prev-close basis — is_without_trading_amt marks which basis the row's
--  pcts were computed against:
--    • TRUE  (FALLBACK, 5-min LIVE pass): the member's LAST 5-min bar
--      close of its latest intraday date BEFORE the live date
--      (self-contained in stats.index_intraday_5min — no basic_stats
--      dependency). Equal-weighted aggregation only.
--    • FALSE (WEIGHTED, yday-ref mode): the member's official prev-day
--      DAILY close from stats.index_basic_stats, computed at tick time
--      (the former ref materialization). A later yday-ref run UPGRADES
--      fallback rows in place (PK upsert — no duplicates).
--
--  Former ref values — computed at USE time from base tables (no
--  materialization):
--    • prev-day close / prev date        → stats.index_basic_stats
--                                          (weighted pass, tick time).
--    • code_trading_amount_weight        → normalized prev-day trading
--      amounts from stats.index_basic_stats (API service, read time).
--    • code_sec_shared_weight            → stats.cross_stats pair grain
--      (API service / pipeline, read time — 2026-09-07 consolidation).
--    • industry_id / is_industry_not_strategy → stats.sec_classification
--      (API services, read time).
--
--  Industry-level aggregates are computed AT QUERY TIME from the tick
--  table (no precomputed industry parent):
--      • weighted : SUM(weight * shared_weight * pct) / SUM(weight *
--                   shared_weight)  -- renormalized over members with
--                   non-NULL pct/weights; shared_weight = 0 ZEROes out
--                   disjoint indices (no composition overlap).
--      • equal    : AVG(pct)
--
--  All pct columns are FRACTIONS (0.01 = 1%), matching the intraday_*
--  market-movements tables and the UI (×100 at render time).
--
--  CONCURRENCY: python -m live.sec_alloc_live_attribution takes a PG
--  advisory lock. The 5-min LIVE process skips when a concurrent
--  instance holds its lock; the yday-ref process waits (bounded).
--
--  Retention: the pipeline prunes dates outside the newest 5 trading
--  dates (RETENTION_DATES in live/sec_alloc_live_attribution).
--
--  Populated by Python (per project rule: INSERTs live in Python code, not
--  raw INSERT...SELECT SQL).
-- ============================================================================

CREATE TABLE IF NOT EXISTS live.sec_alloc_live_attribution (
    code                     TEXT      NOT NULL,
    date                     DATE      NOT NULL,   -- intraday 5-min bar date
    time                     TIME      NOT NULL,   -- intraday 5-min bar time
    sec_type                 TEXT      NOT NULL DEFAULT 'index',
    benchmark_code           TEXT      NOT NULL,
    is_without_trading_amt   BOOLEAN   NOT NULL,   -- TRUE = FALLBACK row (prev-day last 5-min bar close basis, equal-weight only); FALSE = daily-close-basis row (weighted-capable)
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
) PARTITION BY HASH (code);

-- Native hash partitions (32) keyed by code (largest live table, ~7.4GB)
-- Native hash partitions (32) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p31
SELECT public.create_hash_partitions('live', 'sec_alloc_live_attribution', 32);

-- Indexes:
--   1. (benchmark_code, date, time): the reader aggregate filters equality
--      on all three — fully served by this index.
--   The former (benchmark_code, date, time, code) covering index was
--   REDUNDANT and dropped: the aggregation must visit the heap for the pct
--   columns anyway, so the extra `code` suffix bought nothing while
--   costing ~3 GB across the 32 partitions (~2/3 of the table's total
--   index size).
CREATE INDEX IF NOT EXISTS idx_sec_alloc_live_attr_bench_dt
    ON live.sec_alloc_live_attribution (benchmark_code, date, time);
DROP INDEX IF EXISTS idx_sec_alloc_live_attr_ind_bench_dt;

-- ----------------------------------------------------------------------------
--  Idempotent migrations (safe to re-run on fresh or upgraded databases):
--    • ensure toggle columns exist on any pre-existing table
--    • DROP the old strict FK — fallback rows (is_without_trading_amt =
--      TRUE) have no ref parent by design
--    • DROP the consolidated-away ref table (2026-09-08) — its values are
--      recomputed at use time from stats.index_basic_stats /
--      stats.cross_stats / stats.sec_classification
-- ----------------------------------------------------------------------------
ALTER TABLE live.sec_alloc_live_attribution
    ADD COLUMN IF NOT EXISTS is_without_trading_amt BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS is_without_benchmark     BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE live.sec_alloc_live_attribution
    DROP CONSTRAINT IF EXISTS fk_sec_alloc_live_attr_ref;

DROP TABLE IF EXISTS live.sec_alloc_live_prev_ref CASCADE;

-- ----------------------------------------------------------------------------
--  Comments
-- ----------------------------------------------------------------------------
COMMENT ON TABLE  live.sec_alloc_live_attribution IS 'Live per-5-min-tick member attribution (light values only). One row per (code, date, time, sec_type, benchmark_code); tick rows exist ONLY for members classified as index/etf (industry members are indexes with an industry_id); stocks never get tick rows. Stores member + benchmark % vs prev-day close at each tick and the GENERATED diff. is_without_trading_amt marks the prev-close basis: TRUE = fallback (prev-day last 5-min bar close, equal-weight only), FALSE = daily-close basis (official prev-day close from stats.index_basic_stats, computed at tick time — the former live.sec_alloc_live_prev_ref heavy reference table was consolidated away; trading-amount weights and composition-overlap shared weights are computed at READ time from stats.index_basic_stats and stats.cross_stats). Industry-level weighted (SUM weight*shared_weight*pct, renormalized) and equal-weighted (AVG pct) aggregates are computed at query time. All pct columns are fractions (0.01 = 1%).';
COMMENT ON COLUMN live.sec_alloc_live_attribution.is_without_trading_amt IS 'TRUE = FALLBACK row computed WITHOUT daily stats (prev close basis = the member''s last 5-min bar close of the latest intraday date before the live date; equal-weighted aggregation only). FALSE = daily-close-basis row (prev close = official prev-day close from stats.index_basic_stats; weighted-capable via read-time weights). Fallback rows are upgraded in place (PK upsert) by the yday-ref mode''s weighted pass.';
COMMENT ON COLUMN live.sec_alloc_live_attribution.is_without_benchmark IS 'Denormalized toggle column. FALSE = row includes benchmark comparison (pct vs benchmark). TRUE = member-only pct without benchmark comparison.';
COMMENT ON COLUMN live.sec_alloc_live_attribution.code_price_pct_relative_prev_date_close IS 'Member intraday % change vs prev-day close at this tick (FRACTION): stats.index_intraday_5min.close / prev close - 1. Prev close basis: the member''s official prev-day daily close (FALSE rows) or its prev-day last 5-min bar close (fallback rows).';
COMMENT ON COLUMN live.sec_alloc_live_attribution.benchmark_price_pct_relative_prev_date_close IS 'Benchmark intraday % change vs prev-day close at this tick (FRACTION), denormalized so attribution queries need no JOIN to the benchmark series.';
COMMENT ON COLUMN live.sec_alloc_live_attribution.code_price_pct_vs_benchmark_price_pct IS 'GENERATED: member pct - benchmark pct. Positive = member outperforming the benchmark at this tick.';

-- ----------------------------------------------------------------------------
--  Register in live.live_identity
-- ----------------------------------------------------------------------------
INSERT INTO live.live_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('sec_alloc_live_attribution', 'sec_alloc_live_attribution', 'sec_alloc_live_attribution', NOW(),
     'Live per-5-min-tick member attribution under the live schema: light per-tick member + benchmark % vs prev-day close with GENERATED diff in live.sec_alloc_live_attribution (tick rows only for index/etf members). is_without_trading_amt marks the prev-close basis: fallback rows (TRUE, prev-day last 5-min bar close) are written by the 5-min LIVE pass so equal-weighted data flows immediately, and upgraded in place to daily-close-basis rows (FALSE) by the yday-ref mode, which computes prev closes from stats.index_basic_stats at tick time (the former heavy live.sec_alloc_live_prev_ref table was consolidated away — trading-amount weights and composition-overlap shared weights are computed at READ time from stats.index_basic_stats and stats.cross_stats; industry identity from stats.sec_classification). PG advisory locks make the 5-min live process skip and the yday-ref process wait under concurrency. Industry-level weighted (SUM weight*shared_weight*pct, renormalized) and equal-weighted (AVG pct) aggregates are computed at query time. A retention prune (ref/all modes) keeps only the newest 5 trading dates. Sources: stats.sec_classification (member universe), stats.index_basic_stats (prev-day close + trading_amount), stats.index_intraday_5min (tick closes).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
