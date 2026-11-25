-- Daily macro rows from stats.v_macro_daily (macro_identity +
-- macro_basic_stats join) with the date-range filter pushed down to the
-- database (index-driven WHERE). One row per (code, date); columns kept
-- in sync with macro.service.ts transformRow. metal_type is decoded via
-- the caller's labelDecodeSql fragment. Serves GET /api/macro.
-- args:
--   select_columns: fragment — SELECT_COLUMNS column list (macro.service.ts,
--     embeds the macro_metal decode AS metal_label)
--   where_clause: fragment — conditional date-range WHERE
--     ("WHERE date >= $1::date AND date <= $2::date") or ""

    SELECT {{select_columns}}
    FROM stats.v_macro_daily
    {{where_clause}}
    ORDER BY date ASC, code ASC
