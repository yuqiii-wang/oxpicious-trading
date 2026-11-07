-- Pending WEIGHTED member-tick rows for ONE (benchmark, date) — the
-- weighted loader's fetch. Prev closes are computed AT TICK TIME from
-- stats.index_basic_stats: prev_d is ONE resolved prev date (the latest
-- close-bearing date strictly before the live date, resolved ONCE so every
-- join is an exact-date equality), member prev close is the member's DAILY
-- close on prev_d (NaN/zero-filtered; members without a row on prev_d are
-- skipped — they keep their fallback rows), benchmark prev close likewise.
-- Anti-joins the tick table per (code, time) on is_without_trading_amt =
-- FALSE: rows missing entirely OR present only as FALLBACK (TRUE) are
-- (re)fetched — the upsert then upgrades fallback rows in place. Zero
-- closes are the sources' no-quote placeholders — filtered like NULL.
-- args:
--   benchmark_code: value text — the benchmark whose member ticks to fetch
--   live_date: value date — the live date
--   tick_class_types: value text[] — sec_classification types eligible for
--     tick rows (config.TICK_CLASS_TYPES)
--   broad_excluded: value text[] — industry_ids excluded from the member
--     universe (config.BROAD_EXCLUDED)
WITH bench_bars AS (
    SELECT i5.time, i5.close
    FROM stats.index_intraday_5min i5
    WHERE i5.code = {{benchmark_code}}::text
      AND i5.date = {{live_date}}::date
      AND i5.close IS NOT NULL
      AND i5.close <> 0
),
members AS MATERIALIZED (
    SELECT sc.code, sc.type
    FROM stats.sec_classification sc
    WHERE sc.is_active = TRUE
      AND sc.type = ANY({{tick_class_types}}::text[])
      AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
      AND sc.industry_id <> ALL({{broad_excluded}}::text[])
      AND sc.code <> {{benchmark_code}}::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < {{live_date}}::date
      AND close IS NOT NULL
),
member_prev_close AS (
    SELECT b.code, b.close AS prev_close
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.close IS NOT NULL AND b.close::text <> 'NaN' AND b.close <> 0
      AND b.code IN (SELECT code FROM members)
),
bench_prev_close AS (
    SELECT close AS prev_close
    FROM stats.index_basic_stats
    WHERE code = {{benchmark_code}}::text
      AND date = (SELECT d FROM prev_d)
      AND close IS NOT NULL
      AND close::text <> 'NaN'
      AND close <> 0
    LIMIT 1
)
SELECT
    i5.code,
    m.type                          AS sec_type,
    i5.date,
    i5.time,
    i5.close                        AS tick_close,
    mp.prev_close                   AS code_prev_date_close,
    bp.prev_close                   AS benchmark_prev_date_close,
    bb.close                        AS bench_tick_close
FROM stats.index_intraday_5min i5
JOIN members m ON m.code = i5.code
JOIN member_prev_close mp ON mp.code = i5.code
CROSS JOIN bench_prev_close bp
LEFT JOIN bench_bars bb ON bb.time = i5.time
WHERE i5.date = {{live_date}}::date
  AND i5.close IS NOT NULL
  AND i5.close <> 0
  AND NOT EXISTS (
      SELECT 1
      FROM live.sec_alloc_live_attribution t
      WHERE t.benchmark_code = {{benchmark_code}}::text
        AND t.date = i5.date
        AND t.code = i5.code
        AND t.sec_type = m.type
        AND t.time = i5.time
        AND t.is_without_trading_amt = FALSE
  )
ORDER BY i5.code, i5.time
