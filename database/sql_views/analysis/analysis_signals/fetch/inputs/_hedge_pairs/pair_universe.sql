-- The hedge_pairs family's pair universe — ALL INDUSTRY PAIRS (the
-- 2026-11-07 all-industries widening, user-directed "for hedge pairs
-- add all industries (would have (n ** 2) / 2 pairs of n industries)"):
-- every unordered pair of the pool-'all' industry composites that
-- carry close data, n industries -> n(n-1)/2 pairs. Each pair rides
-- its FRESHEST SCORED offsets reading (analysis_composites.
-- industry_corr_benchmark_offsets, pool 'all', interval 20, the
-- score-bearing rows only — the build's latest readings can carry
-- NULL scores, so "freshest" means freshest NON-NULL here, the
-- scored-disjoint resolver's semantics) as
-- METADATA — score/rho, NULL where the offsets carry no scored
-- reading — never a filter (the scored-disjoint era is retired: the
-- measured gate self-picks the tradable pairs on evidence). The
-- direction is normalized (least/greatest) so (A,B) and (B,A)
-- readings dedup onto ONE unordered pair's freshest row.
-- args:
--   industry_stats_table: fragment — stats.industry_basic_stats
--   offsets_table: fragment — the offsets estimates table
--   pool_size: value int — the composites/offsets pool_size ('all' = 3)
--   interval: value int — the offsets "interval" (20)
        WITH industries AS (
            SELECT DISTINCT industry_id
            FROM {{industry_stats_table}}
            WHERE pool_size = {{pool_size}}
              AND mean_close IS NOT NULL
        ),
        freshest AS (
            SELECT DISTINCT ON (lo_id, hi_id)
                   least(industry_id, benchmark_industry_id) AS lo_id,
                   greatest(industry_id, benchmark_industry_id) AS hi_id,
                   opposite_score_ma252_252d::float8 AS score,
                   offset_sub_corr_ma252_252d::float8 AS rho
            FROM {{offsets_table}}
            WHERE pool_size = {{pool_size}}
              AND "interval" = {{interval}}
              AND opposite_score_ma252_252d IS NOT NULL
            ORDER BY least(industry_id, benchmark_industry_id),
                     greatest(industry_id, benchmark_industry_id),
                     start_date DESC
        )
        SELECT a.industry_id AS leg_a,
               b.industry_id AS leg_b,
               f.score,
               f.rho
        FROM industries a
        JOIN industries b ON a.industry_id < b.industry_id
        LEFT JOIN freshest f
               ON f.lo_id = a.industry_id
              AND f.hi_id = b.industry_id
