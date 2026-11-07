-- Intraday Movements — fallback industry % change: equal-weight industry
-- aggregates computed on-the-fly from the RAW intraday table when the
-- live tick table has no rows for the target date (the live pipeline is
-- LATEST-date-scoped by design). Mirrors the live pipeline's ref-less
-- FALLBACK semantics: member universe = active classification
-- indices/ETFs with a non-BROAD industry (benchmark itself excluded);
-- prev close = the member's latest close strictly before the target date
-- from stats.index_basic_stats; pct = close/prev_close - 1 (FRACTION,
-- same scale as live rows).
-- Serves GET /api/live-data/intraday-movements
-- args:
--   benchmark_code: value text — the benchmark index code
--   date: value date — the target trading date
WITH cls AS (
    SELECT DISTINCT ON (code)
        code,
        industry_id,
        is_industry_not_strategy
    FROM stats.sec_classification
    WHERE is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
    ORDER BY code
),
universe AS (
    SELECT
        sc.code,
        cls.industry_id,
        COALESCE(cls.is_industry_not_strategy, TRUE) AS is_industry_not_strategy
    FROM stats.sec_classification sc
    JOIN cls ON cls.code = sc.code
    WHERE sc.is_active = TRUE
      AND sc.type IN ('index', 'etf')
      AND cls.industry_id NOT LIKE 'BROAD_%'
      AND sc.code <> {{benchmark_code}}::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < {{date}}::date AND close IS NOT NULL
),
member_prev_close AS (
    SELECT b.code, b.close AS prev_close
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.close IS NOT NULL AND b.close::text <> 'NaN'
      AND b.code IN (SELECT code FROM universe)
),
bench_prev_close AS (
    SELECT close AS prev_close
    FROM stats.index_basic_stats
    WHERE code = {{benchmark_code}}::text
      AND date = (SELECT d FROM prev_d)
      AND close IS NOT NULL AND close::text <> 'NaN'
    LIMIT 1
),
bench_ticks AS (
    SELECT bb.time, (bb.close / bpc.prev_close - 1) AS bench_pct
    FROM stats.index_intraday_5min bb
    CROSS JOIN bench_prev_close bpc
    WHERE bb.code = {{benchmark_code}}::text
      AND bb.date = {{date}}::date
      AND bb.close IS NOT NULL AND bb.close::text <> 'NaN'
),
member_ticks AS (
    SELECT i5.code, i5.time,
           (i5.close / mp.prev_close - 1) AS pct
    FROM stats.index_intraday_5min i5
    JOIN member_prev_close mp ON mp.code = i5.code
    WHERE i5.date = {{date}}::date
      AND i5.close IS NOT NULL AND i5.close::text <> 'NaN'
)
SELECT
    m.time::text                            AS time,
    u.industry_id,
    BOOL_OR(u.is_industry_not_strategy)     AS is_industry_not_strategy,
    COALESCE(sc.industry_label, u.industry_id) AS industry_label,
    AVG(m.pct)::float8                      AS industry_price_pct,
    (AVG(m.pct) - MAX(bt.bench_pct))::float8 AS industry_price_pct_vs_benchmark
FROM member_ticks m
JOIN universe u ON u.code = m.code
LEFT JOIN bench_ticks bt ON bt.time = m.time
LEFT JOIN LATERAL (
    SELECT industry_label
    FROM stats.sec_classification
    WHERE industry_id = u.industry_id AND type = 'index'
    LIMIT 1
) sc ON true
GROUP BY m.time, u.industry_id, sc.industry_label
ORDER BY m.time, u.industry_id
