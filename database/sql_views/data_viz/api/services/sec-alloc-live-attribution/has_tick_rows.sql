-- Existence probe: does the (benchmark, date) have ANY tick rows? A plain
-- indexed lookup (idx_sec_alloc_live_attr_bench_dt) — runs on every
-- request, so it must stay O(1)-ish. A non-TRUE answer triggers the
-- on-demand backfill. Serves GET /api/live-data/sec-alloc-live/attribution.
-- args:
--   benchmark_code: value text — the benchmark index code
--   date: value text — tick date (YYYY-MM-DD)
SELECT EXISTS (
    SELECT 1
    FROM live.sec_alloc_live_attribution
    WHERE benchmark_code = {{benchmark_code}}::text
      AND date = {{date}}::date
) AS has_tick_rows
