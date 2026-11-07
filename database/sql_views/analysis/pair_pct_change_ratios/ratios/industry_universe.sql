-- pair_pct_change_ratios — industry ids with at least one active
-- type='index' member carrying a close (subject AND benchmark
-- universe; self-industry pairs excluded downstream).
-- args: none

        SELECT DISTINCT sc.industry_id
        FROM stats.index_basic_stats b
        JOIN stats.sec_classification sc
            ON sc.code = b.code AND sc.type = 'index'
        WHERE sc.is_active = TRUE AND b.close IS NOT NULL
          AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
    