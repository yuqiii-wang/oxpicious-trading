-- Industry-attributions runner sanity summary — row counts / date spans /
-- average weights per (benchmark_code, attribution_type) from
-- analysis.industry_attributions, broad-market flag joined from
-- stats.sec_index_tags (log output only, top 40, broad-market first).
-- args: none
        SELECT ia.benchmark_code,
               ia.attribution_type,
               BOOL_OR(sit.is_broad_market) AS is_broad,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT ia.industry_id) AS n_industries,
               MIN(ia.date) AS first_date,
               MAX(ia.date) AS last_date,
               ROUND(AVG(ia.industry_shared_weight), 4) AS avg_isw,
               ROUND(AVG(ia.benchmark_shared_weight), 4) AS avg_bsw,
               COUNT(*) FILTER (WHERE ia.industry_shared_weight = 0
                                 AND ia.benchmark_shared_weight = 0)
                   AS n_zero_overlap
        FROM analysis.industry_attributions ia
        LEFT JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
        GROUP BY ia.benchmark_code, ia.attribution_type
        ORDER BY is_broad DESC, ia.benchmark_code, ia.attribution_type
        LIMIT 40
