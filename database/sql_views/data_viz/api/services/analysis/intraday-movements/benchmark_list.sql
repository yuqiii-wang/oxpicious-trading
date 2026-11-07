-- Intraday Movements — broad-market benchmark dropdown list: curated
-- benchmarks (benchmark_broadmarket tag) that appear in the live tick
-- table OR have pair-grain attribution history + intraday data, plus the
-- legacy is_broad_market fallback so the dropdown is never empty;
-- enriched with display name + is_broad_market flag. The history CTE
-- variant (summary rollup UNNEST vs full-table DISTINCT scan) is picked
-- at call time and passed as a fragment.
-- Serves GET /api/live-data/intraday-movements/benchmarks
-- args:
--   all_curated_with_data: fragment — buildAllCuratedWithDataCte(useSummary)
WITH
-- The curated broad-market benchmark set: indices carrying the
-- hand-authored BROAD/benchmark_broadmarket tag.
curated AS (
    SELECT DISTINCT t.code AS benchmark_code
    FROM stats.sec_index_tags t
    WHERE t.industry_id = 'benchmark_broadmarket'
),
processed AS (
    SELECT DISTINCT a.benchmark_code
    FROM live.sec_alloc_live_attribution a
    WHERE a.benchmark_code IN (SELECT benchmark_code FROM curated)
),{{all_curated_with_data}},
bench_codes AS (
    SELECT benchmark_code FROM processed
    UNION
    SELECT benchmark_code FROM all_curated_with_data
),
-- Legacy fallback: before the classification build materializes the
-- benchmark_broadmarket tag into stats.sec_index_tags, fall back to the
-- old is_broad_market selection so the dropdown is never empty.
legacy_broad AS (
    SELECT DISTINCT t.code AS benchmark_code
    FROM stats.sec_index_tags t
    WHERE t.is_broad_market = TRUE
),
final_codes AS (
    SELECT benchmark_code FROM bench_codes
    UNION ALL
    SELECT lb.benchmark_code
    FROM legacy_broad lb
    WHERE NOT EXISTS (SELECT 1 FROM stats.sec_index_tags t2 WHERE t2.industry_id = 'benchmark_broadmarket')
),
enriched AS (
    SELECT
        bc.benchmark_code,
        (SELECT name FROM stats.index_identity WHERE code = bc.benchmark_code ORDER BY date DESC LIMIT 1) AS benchmark_name,
        (SELECT BOOL_OR(is_broad_market) FROM stats.sec_index_tags WHERE code = bc.benchmark_code) AS is_broad_market
    FROM final_codes bc
)
SELECT * FROM enriched
ORDER BY benchmark_code
