-- Market Crowdedness chart — display-name lookup from the sec_type's
-- identity source (latest identity row per code).
-- Serves GET /api/analysis/market-crowdedness/chart
-- args:
--   codes: value text[] — codeVariants(target) (bare + suffixed forms)
--   identity_table: fragment — IDENTITY_TABLE[secType]
    SELECT DISTINCT ON (code) code, name
    FROM {{identity_table}}
    WHERE code = ANY({{codes}}::text[])
    ORDER BY code, date DESC
