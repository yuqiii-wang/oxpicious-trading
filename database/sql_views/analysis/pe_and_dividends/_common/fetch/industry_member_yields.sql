-- pe_and_dividends industry stage — member-index dividend yields
-- (analysis.dividends sec_type=1) joined to their industry
-- membership (active sec_classification indexes with an
-- industry_id); the industry stage cross-sectional-means them.
-- args: none

        SELECT
            sc.industry_id AS code,
            extract(epoch from d.date)::float8 AS date,
            d.dividend_yield::float8 AS dividend_yield
        FROM stats.sec_classification sc
        JOIN analysis.dividends d
          ON d.code = sc.code AND d.sec_type = 1
        WHERE sc.type = 'index'
          AND sc.is_active = TRUE
          AND sc.industry_id IS NOT NULL
          AND sc.industry_id <> ''
        