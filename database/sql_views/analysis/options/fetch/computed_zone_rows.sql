-- options bands pipeline — the computed wall_type='zone' rows of
-- analysis.options_walls (walls step output) scoped to codes with
-- live option terms — the bands exit-quality context input.
-- args:
--   sec_filter: fragment — the venue filter (empty, or
--       "AND t.underlying_target_type = '<TYPE>'" from
--       _sec_type_where)

        SELECT
            extract(epoch from w.date)::float8 AS date,
            w.underlying_code,
            w.option_type,
            w.wall_low::float8 AS wall_low,
            w.wall_high::float8 AS wall_high,
            w.wall_oi::float8 AS wall_oi,
            w.mass_share::float8 AS mass_share,
            w.state,
            w.strength_score::float8 AS strength_score
        FROM analysis.options_walls w
        WHERE w.wall_type = 'zone'
          AND EXISTS (
              SELECT 1
              FROM stats.options_terms t
              JOIN stats.options_strike k
                ON k.date = t.date AND k.contract_code = t.contract_code
              WHERE t.date = w.date
                AND t.underlying_code = w.underlying_code
                {{sec_filter}}
          )
        ORDER BY w.underlying_code, w.date
    