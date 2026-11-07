-- Sec-Alloc live attribution — per-industry aggregates at ONE 5-min tick
-- (live.sec_alloc_live_attribution). Weight semantics (computed ONCE per
-- request in the CTEs — the same once-per-(benchmark, date) normalization
-- the pipeline's former ref build performed, now at read time):
--   prev_d          — ONE resolved prev date: latest close-bearing
--                     stats.index_basic_stats date strictly before the tick
--                     date; every prev-value join is an exact-date equality.
--   member_prev_amt — member's REAL prev-day trading_amount (literal NaN
--                     numerics excluded via ::text <> 'NaN'). Members
--                     without one keep weight NULL → excluded from the
--                     weighted aggregate (renormalized denominator), still
--                     counted by AVG (equal).
--   weights         — amount / Σ amounts (Σ over members with a real
--                     amount; Σ = 1). Only tick-eligible members
--                     (index/etf) need weights: stocks never carry tick
--                     rows.
--   cs_weight       — composition overlap from the
--                     stats.cross_stats_benchmark_weight_summary rollup:
--                     latest row per member pair, snapshot-constant per
--                     pair. Fallback (no rollup rows — fresh DB before the
--                     first builds.cross_stats run): the former live
--                     DISTINCT ON aggregate over stats.cross_stats.
--                     NaN → 0, NULL → 0 (zero-overlap).
-- Serves GET /api/live-data/sec-alloc-live/attribution.
-- args:
--   benchmark_code: value text — the benchmark index code ($1)
--   date: value text — tick date (YYYY-MM-DD) ($2)
--   time: value text — tick time (HH:MM:SS) ($3)
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
    SELECT code FROM stats.sec_classification
    WHERE is_active = TRUE
      AND type IN ('index', 'etf')
      AND industry_id IS NOT NULL AND industry_id <> ''
      AND industry_id NOT LIKE 'BROAD_%'
      AND code <> {{benchmark_code}}::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < {{date}}::date
      AND close IS NOT NULL
),
member_prev_amt AS (
    SELECT b.code, b.trading_amount AS amt
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.trading_amount IS NOT NULL AND b.trading_amount::text <> 'NaN'
      AND b.code IN (SELECT code FROM universe)
),
weights AS (
    SELECT m.code, m.amt / t.total AS w
    FROM member_prev_amt m
    CROSS JOIN (SELECT SUM(amt) AS total FROM member_prev_amt) t
    WHERE t.total > 0
),
cs_weight_rollup AS (
    SELECT ws.code, ws.code_sec_shared_weight
    FROM stats.cross_stats_benchmark_weight_summary ws
    WHERE ws.sec_type = 'index'
      AND ws.benchmark_code = {{benchmark_code}}::text
),
cs_weight AS (
    SELECT r.code, r.code_sec_shared_weight
    FROM cs_weight_rollup r
    WHERE EXISTS (SELECT 1 FROM cs_weight_rollup)
    UNION ALL
    SELECT DISTINCT ON (cs.code) cs.code, cs.code_sec_shared_weight
    FROM stats.cross_stats cs
    WHERE cs.sec_type = 'index'
      AND cs.benchmark_code = {{benchmark_code}}::text
      AND NOT EXISTS (SELECT 1 FROM cs_weight_rollup)
),
base AS (
    SELECT
        cls.industry_id,
        COALESCE(cls.is_industry_not_strategy, TRUE)    AS is_industry_not_strategy,
        w.w,
        COALESCE(NULLIF(csw.code_sec_shared_weight, 'NaN'::numeric), 0) AS sw,
        a.code_price_pct_relative_prev_date_close       AS pct
    FROM live.sec_alloc_live_attribution a
    JOIN cls ON cls.code = a.code
    LEFT JOIN weights w ON w.code = a.code
    LEFT JOIN cs_weight csw ON csw.code = a.code
    WHERE a.benchmark_code = {{benchmark_code}}::text
      AND a.date = {{date}}::date
      AND a.time = {{time}}::time
)
SELECT
    industry_id,
    BOOL_OR(is_industry_not_strategy)                     AS is_industry_not_strategy,
    COALESCE(
        (SUM(w * sw * pct)
            / NULLIF(SUM(w * sw) FILTER (WHERE pct IS NOT NULL AND w IS NOT NULL AND sw IS NOT NULL), 0)
        )::float8,
        0
    )                                                      AS weighted_pct,
    AVG(pct)::float8                                      AS equal_pct,
    COUNT(*)                                              AS member_count
FROM base
WHERE industry_id IS NOT NULL
GROUP BY industry_id
ORDER BY weighted_pct DESC
