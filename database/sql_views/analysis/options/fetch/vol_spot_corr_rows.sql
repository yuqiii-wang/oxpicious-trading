-- options vol-vs-spot corr pipeline — every options_vol_index row
-- LEFT-joined to options_spot_daily's close (NaN where the spot
-- day is missing), scoped to underlyings with option terms.
-- args:
--   sec_filter: fragment — the venue filter (empty, or
--       "AND t.underlying_target_type = '<TYPE>'" from
--       _sec_type_where)

        SELECT extract(epoch from v.date)::float8 AS date,
               v.underlying_code,
               v.vol_index_30d::float8 AS vol_index_30d,
               s.close::float8 AS spot_close
        FROM analysis.options_vol_index v
        LEFT JOIN analysis.options_spot_daily s
          ON s.underlying_code = v.underlying_code AND s.date = v.date
        WHERE EXISTS (
            SELECT 1
            FROM stats.options_terms t
            WHERE t.underlying_code = v.underlying_code
              {{sec_filter}}
        )
        ORDER BY v.underlying_code, v.date
    