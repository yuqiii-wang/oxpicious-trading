-- Intraday Movements prev-day OHLC — the RAW daily OHLC of the PREVIOUS
-- trading day (strict previous weekday of the live date: Mon → Fri,
-- Sun → Fri, otherwise −1 day) for the benchmark itself + EVERY member
-- index of its universe (active stats.sec_classification indices/ETFs
-- with a non-BROAD industry — benchmark excluded), each carrying its
-- industry_id; per code the stats.index_basic_stats row at EXACTLY the
-- prev weekday with clean (non-NULL, non-NaN, close > 0) OHLC via LEFT
-- JOIN LATERAL (dirty/missing rows are skipped). When yesterday's data is
-- missing the benchmark row comes back NULL — the client renders an empty
-- prev-day tick instead of silently falling back to an older day.
-- Serves GET /api/live-data/intraday-movements/prev-day-ohlc
-- args:
--   benchmark_code: value text — the benchmark index code
--   date: value date — the live date (YYYY-MM-DD)
WITH params AS (
    -- Strict previous WEEKDAY (Sun → Fri, Mon → Fri, otherwise −1 day):
    -- the boolean casts add the extra skip days.
    SELECT {{date}}::date - 1
         - (extract(dow FROM {{date}}::date)::int = 0)::int
         - 2 * (extract(dow FROM {{date}}::date)::int = 1)::int AS prev_date
),
members AS (
    SELECT DISTINCT ON (code)
        code,
        industry_id
    FROM stats.sec_classification
    WHERE is_active = TRUE
      AND type IN ('index', 'etf')
      AND industry_id IS NOT NULL AND industry_id <> ''
      AND industry_id NOT LIKE 'BROAD_%'
      AND code <> {{benchmark_code}}::text
    ORDER BY code
),
codes AS (
    SELECT code, industry_id FROM members
    UNION ALL
    SELECT {{benchmark_code}}::text, NULL::text
),
prev_ohlc AS (
    SELECT
        c.code,
        c.industry_id,
        p.prev_date,
        b.open,
        b.high,
        b.low,
        b.close
    FROM codes c
    CROSS JOIN params p
    LEFT JOIN LATERAL (
        SELECT open, high, low, close
        FROM stats.index_basic_stats
        WHERE code = c.code
          AND date = p.prev_date
          AND close IS NOT NULL AND close::text <> 'NaN' AND close > 0
          AND open  IS NOT NULL AND open::text  <> 'NaN'
          AND high  IS NOT NULL AND high::text  <> 'NaN'
          AND low   IS NOT NULL AND low::text   <> 'NaN'
        LIMIT 1
    ) b ON true
    ORDER BY c.code
)
SELECT
    p.code,
    (SELECT name FROM stats.index_identity ii
      WHERE ii.code = p.code ORDER BY ii.date DESC LIMIT 1) AS code_name,
    p.industry_id,
    p.prev_date,
    p.open,
    p.high,
    p.low,
    p.close
FROM prev_ohlc p
ORDER BY p.code
