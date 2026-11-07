-- Daily debt rows from stats.v_debt_daily (debt_identity + omo + repo +
-- outright_repo + mlf + shibor + treasury join) with the date-range filter
-- pushed down to the database (index-driven WHERE). Columns kept in sync
-- with debt.service transformRow. Serves GET /api/debt/daily.
-- args:
--   select_columns: fragment — SELECT_COLUMNS column list (debt.service.ts)
--   where_clause: fragment — conditional date-range WHERE
--     ("WHERE date >= $1::date AND date <= $2::date") or ""

    SELECT {{select_columns}}
    FROM stats.v_debt_daily
    {{where_clause}}
    ORDER BY date ASC
