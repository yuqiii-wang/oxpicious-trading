-- Year-month per contract of ONE product (distributed column — cheap to
-- fetch). Serves GET /api/futures combined.
-- args:
--   product_code: value text — the product to fetch

    SELECT DISTINCT code, contract_year_month
    FROM stats.futures_identity
    WHERE product_code = {{product_code}}
