-- Latest-closed-session coverage per daily-bar family — the
-- LatestSessionBackfillJob's verdict input: covered (distinct codes
-- with close on the session) vs expected (the previous session's
-- coverage). One row per family; the Python decides partial
-- (covered < expected * 0.9 — the daily-check threshold convention).
-- args:
--   session: value text — the latest closed session (ISO date)
--   prev: value text — the session before it (ISO date)
SELECT 'index'::text AS family,
       (SELECT COUNT(DISTINCT code) FROM stats.index_basic_stats
        WHERE close IS NOT NULL AND date = {{session}}::date) AS covered,
       (SELECT COUNT(DISTINCT code) FROM stats.index_basic_stats
        WHERE close IS NOT NULL AND date = {{prev}}::date) AS expected
UNION ALL
SELECT 'stock'::text AS family,
       (SELECT COUNT(DISTINCT code) FROM stats.stock_basic_stats
        WHERE close IS NOT NULL AND date = {{session}}::date) AS covered,
       (SELECT COUNT(DISTINCT code) FROM stats.stock_basic_stats
        WHERE close IS NOT NULL AND date = {{prev}}::date) AS expected
UNION ALL
SELECT 'etf'::text AS family,
       (SELECT COUNT(DISTINCT code) FROM stats.etf_basic_stats
        WHERE close IS NOT NULL AND date = {{session}}::date) AS covered,
       (SELECT COUNT(DISTINCT code) FROM stats.etf_basic_stats
        WHERE close IS NOT NULL AND date = {{prev}}::date) AS expected
UNION ALL
SELECT 'futures'::text AS family,
       (SELECT COUNT(DISTINCT code) FROM stats.futures_basic_stats
        WHERE close IS NOT NULL AND date = {{session}}::date) AS covered,
       (SELECT COUNT(DISTINCT code) FROM stats.futures_basic_stats
        WHERE close IS NOT NULL AND date = {{prev}}::date) AS expected
