-- Vol index (30d model-free, VIX-style) — analysis.options_vol_index.
-- `underlying` optional — omitting it returns every underlying's series for
-- the cross-asset chart. Serves GET /api/options/vol-index.
-- args:
--   where_clause: fragment — optional scope WHERE
--     ("WHERE underlying_code = $1" plus date bounds) or ""
    SELECT date, underlying_code,
           near_expiry_date, far_expiry_date,
           dte_near, dte_far,
           var_near, var_far, variance_30d, vol_index_30d
    FROM analysis.options_vol_index
    {{where_clause}}
    ORDER BY date ASC, underlying_code ASC
