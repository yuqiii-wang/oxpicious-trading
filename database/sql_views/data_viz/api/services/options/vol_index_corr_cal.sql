-- The interpolation calendar for the vol-index vs spot corr endpoint:
-- every stored vol-index session per underlying (the same basis the corr
-- table was sampled on). The endpoint linearly interpolates each corr
-- window onto every session of this calendar in session-ordinal space
-- (inside the stored span only). Serves GET /api/options/vol-index-corr.
-- args:
--   where_clause: fragment — optional scope WHERE
--     ("WHERE underlying_code = $1") or ""
    SELECT date, underlying_code
    FROM analysis.options_vol_index
    {{where_clause}}
    ORDER BY underlying_code ASC, date ASC
