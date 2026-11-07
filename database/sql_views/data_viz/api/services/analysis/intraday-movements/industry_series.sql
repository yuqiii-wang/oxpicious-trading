-- Intraday Movements — industry % change per (tick, industry) for ALL
-- industries, computed at query time from the live tick table
-- (equal-weight AVG of member pcts — same semantics as the retired
-- analysis pre-compute). Excludes BROAD_* (broad-market indices that are
-- themselves benchmarks, not real industries). Industry identity =
-- stats.sec_classification (latest ACTIVE row per code). diff = AVG(member
-- pct) − benchmark pct at the tick so the UI top-plot shade can be driven
-- directly.
-- Serves GET /api/live-data/intraday-movements
-- args:
--   date: value date — the target trading date
--   benchmark_code: value text — the benchmark index code
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
base AS (
    SELECT
        a.time,
        a.code_price_pct_relative_prev_date_close          AS pct,
        a.benchmark_price_pct_relative_prev_date_close     AS bench_pct,
        cls.industry_id,
        COALESCE(cls.is_industry_not_strategy, TRUE)       AS is_industry_not_strategy
    FROM live.sec_alloc_live_attribution a
    JOIN cls ON cls.code = a.code
    WHERE a.benchmark_code = {{benchmark_code}}::text
      AND a.date = {{date}}::date
)
SELECT
    b.time::text                     AS time,
    b.industry_id,
    BOOL_OR(b.is_industry_not_strategy) AS is_industry_not_strategy,
    COALESCE(sc.industry_label, b.industry_id) AS industry_label,
    AVG(b.pct)::float8               AS industry_price_pct,
    (AVG(b.pct) - MAX(b.bench_pct))::float8
                                     AS industry_price_pct_vs_benchmark
FROM base b
LEFT JOIN LATERAL (
    SELECT industry_label
    FROM stats.sec_classification
    WHERE industry_id = b.industry_id AND type = 'index'
    LIMIT 1
) sc ON true
WHERE b.industry_id IS NOT NULL
  AND b.industry_id NOT LIKE 'BROAD_%'
GROUP BY b.time, b.industry_id, sc.industry_label
ORDER BY b.time, b.industry_id
