-- Industry-attributions step guard — count of non-broad member indices
-- with composition data (the member-index INSERT runs even when the
-- broad-market source grain is empty; only exit when BOTH are empty).
-- args: none
    SELECT COUNT(DISTINCT sc.code) AS n
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
