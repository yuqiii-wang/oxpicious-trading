-- Correlations step (analysis.market_beta internal pipeline) — FILTERED
-- mode (python -m analysis.market_beta.corr --industry/--code): load the
-- mean_close history for the given industry_ids only (pairs form within
-- the set). Only non-NULL mean_close rows are useful — NULL rows mean no
-- member indices contributed to that (date, industry, pool) slice.
-- args:
--   industry_ids: value text[] — the industry_ids to restrict the load to
            SELECT extract(epoch from date)::float8 AS date,
                   industry_id, pool_size, mean_close
            FROM stats.industry_basic_stats
            WHERE mean_close IS NOT NULL
              AND industry_id = ANY({{industry_ids}})
            ORDER BY industry_id, pool_size, date
