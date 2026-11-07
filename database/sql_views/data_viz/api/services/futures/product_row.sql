-- Header info for ONE futures product (identity row, contract_type decoded
-- to its TEXT label). Serves GET /api/futures combined.
-- args:
--   product_code: value text — the product to fetch
--   contract_type_decode: fragment — labelDecodeSql("contract_type", "contract_type")

    SELECT product_code, name,
           {{contract_type_decode}} AS contract_type,
           underlying_code, underlying_name
    FROM stats.futures_identity
    WHERE product_code = {{product_code}}
    LIMIT 1
