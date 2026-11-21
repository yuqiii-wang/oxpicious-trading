-- Volatility — display-name lookup from the sec_type's identity source
-- (latest identity row per code; the vol table carries no name). The
-- table is passed as a fragment arg (the pipeline is structured for
-- etf/index/stock).
-- Serves GET /api/analysis/volatility/series
-- args:
--   codes: value text[] — codeVariants(target) (bare + suffixed forms)
--   identity_table: fragment — IDENTITY_TABLE[secType]
    SELECT DISTINCT ON (code) code, name
    FROM {{identity_table}}
    WHERE code = ANY({{codes}}::text[])
    ORDER BY code, date DESC
