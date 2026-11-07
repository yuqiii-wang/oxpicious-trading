-- Futures product list — DISTINCT products from stats.futures_identity
-- (CFFEX IC/IF/IH/IM/T/TF/TL/TS + offshore CN/MCA A50 futures). Serves
-- GET /api/futures (products list).
-- args:
--   contract_type_decode: fragment — labelDecodeSql("contract_type", "contract_type")

    SELECT DISTINCT
      product_code,
      name,
      {{contract_type_decode}} AS contract_type,
      underlying_code,
      underlying_name
    FROM stats.futures_identity
    ORDER BY product_code
