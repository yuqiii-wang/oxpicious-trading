-- Market-beta chart — broad-market benchmark close series (one flat row per
-- benchmark code x date; grouped client-side, rebased to 100 client-side).
-- args:
--   benchmark_codes: value text[] — the benchmark index codes to fetch
SELECT code, date, close FROM stats.index_basic_stats
     WHERE code = ANY({{benchmark_codes}}::text[]) ORDER BY code, date ASC
