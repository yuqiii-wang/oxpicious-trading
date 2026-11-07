-- Stored corr samples for the vol-index vs spot interpolation (Pearson
-- corr of daily log-changes over trailing 20/60/255 sessions). Loaded
-- WITHOUT date bounds: interpolation inside [start, end] needs the
-- surrounding samples, so the window filters the OUTPUT rows, not the
-- fetch. The stored table samples a 5-session stride grid through history
-- plus the daily tail. Serves GET /api/options/vol-index-corr.
-- args:
--   where_clause: fragment — optional scope WHERE
--     ("WHERE underlying_code = $1", same arg list as the calendar query)
--     or ""
    SELECT date, underlying_code, corr_20d, corr_60d, corr_255d
    FROM analysis.options_vol_index_vs_spot_price_corr
    {{where_clause}}
    ORDER BY date ASC, underlying_code ASC
