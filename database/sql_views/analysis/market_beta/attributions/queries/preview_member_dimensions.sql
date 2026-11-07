-- Industry-attributions step preview — distinct industries + member
-- indices that will appear in the member-index rows (non-broad,
-- composition-backed).
-- args: none
    SELECT
        COUNT(DISTINCT cls.industry_id) AS n_industries,
        COUNT(DISTINCT sc.code) AS n_member_indices
    FROM stats.sec_composition sc
    JOIN stats.sec_classification cls
        ON cls.code = sc.code AND cls.type = 'index'
    WHERE sc.source_type = 'index'
      AND sc.stock_code IS NOT NULL
      AND cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      AND cls.is_active = TRUE
      AND cls.is_industry_not_strategy = TRUE
      AND sc.code NOT IN (
          SELECT code FROM stats.sec_index_tags WHERE is_broad_market = TRUE
      )
