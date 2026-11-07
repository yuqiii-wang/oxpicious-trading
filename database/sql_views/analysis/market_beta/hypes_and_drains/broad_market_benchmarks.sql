-- Hypes & drains step (analysis.market_beta internal pipeline) — all
-- broad-market benchmark codes that have industry_attributions data (the
-- SAME set offered by the Benchmark Attribution dropdown). Filter on
-- attribution_type='trading_amt' to avoid duplicate benchmarks (each
-- benchmark now appears twice — once per attribution_type).
-- args: none
    SELECT DISTINCT ia.benchmark_code
    FROM analysis.industry_attributions ia
    JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
    WHERE sit.is_broad_market = TRUE
      AND ia.attribution_type = 'trading_amt'
    ORDER BY ia.benchmark_code
