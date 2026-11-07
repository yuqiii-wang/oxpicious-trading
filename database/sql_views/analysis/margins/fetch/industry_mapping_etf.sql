-- margins — ETF code to industry_id mapping via the two-hop
-- parent_index_code join (all index types included so broad-market
-- and strategy ETFs contribute).
-- args: none

            SELECT
                e.code,
                idx.industry_id,
                idx.industry_label,
                e.parent_index_weight
            FROM stats.sec_classification e
            JOIN LATERAL (
                SELECT DISTINCT industry_id, industry_label
                FROM stats.sec_classification
                WHERE type = 'index'
                  AND is_active = TRUE
                  AND industry_id IS NOT NULL
                  AND industry_id <> ''
                  AND code = e.parent_index_code
            ) idx ON TRUE
            WHERE e.type = 'etf'
              AND e.parent_index_is_primary = TRUE
              AND e.parent_index_code <> ''
            