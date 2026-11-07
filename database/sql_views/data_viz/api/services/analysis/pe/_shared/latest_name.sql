-- PE & Dividend valuation — latest-name lookup for one code (all variants)
-- from the sec_type's identity table (first row wins; callers read
-- nameRows[0]). Shared by the chart and streaks fetches.
-- Serves GET /api/analysis/pe/chart, /api/analysis/pe/streaks.
-- args:
--   codes: value text[] — the code's variants (codeVariants)
--   identity_table: fragment — SEC_SOURCES[sec_type].identityTable
    SELECT DISTINCT ON (code) code, name
    FROM {{identity_table}}
    WHERE code = ANY({{codes}}::text[])
    ORDER BY code, date DESC
