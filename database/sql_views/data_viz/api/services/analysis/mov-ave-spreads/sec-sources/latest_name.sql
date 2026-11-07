-- MA-Spread display-name lookup — the latest identity row across the
-- code's exchange-suffix variants, from the sec_type-appropriate identity
-- table. DISTINCT ON (code) + ORDER BY code, date DESC walks the
-- (code, date) PK backwards PER CODE — one index probe per variant; the
-- naive ORDER BY date DESC LIMIT 1 form drove a date-index backward scan
-- that filtered EVERY row of every touched partition (~470k rows/partition,
-- 4.4s measured 2026-09-29); the subselect wrapper picks the
-- overall-latest variant's row.
-- Serves GET /api/analysis/mov-ave-spread/chart/pairs (display name).
-- args:
--   codes: value text[] — the code variants to look up (codeVariants)
--   identity_table: fragment — SEC_SOURCES[sec_type].identityTable
    SELECT name
    FROM (
      SELECT DISTINCT ON (code) code, name, date
      FROM {{identity_table}}
      WHERE code = ANY({{codes}}::text[])
      ORDER BY code, date DESC
    ) latest
    ORDER BY date DESC
    LIMIT 1
