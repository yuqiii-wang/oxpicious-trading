-- margins index-series build — sec_classification routing rows
-- (type in stock/etf/index: branch routing, parent weights and
-- the index_code -> industry_id map derive in pandas).
-- args: none

        SELECT
            code,
            type,
            parent_index_code,
            parent_index_weight,
            parent_index_is_primary,
            industry_id
        FROM stats.sec_classification
        WHERE type IN ('stock', 'etf', 'index')
        