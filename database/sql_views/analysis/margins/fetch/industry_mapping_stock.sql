-- margins — stock code to industry_id mapping (primary
-- classification row carries the industry directly).
-- args: none

            SELECT
                code,
                industry_id,
                industry_label,
                parent_index_weight
            FROM stats.sec_classification
            WHERE type = 'stock'
              AND parent_index_is_primary = TRUE
              AND industry_id IS NOT NULL
              AND industry_id <> ''
            