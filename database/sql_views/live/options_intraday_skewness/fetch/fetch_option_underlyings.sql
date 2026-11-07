-- (underlying_code, underlying_target_type) of the latest stats.options_terms
-- snapshot — the option universe the pipeline computes series for.
-- args: (none)
        SELECT DISTINCT underlying_code, underlying_target_type
        FROM stats.options_terms
        WHERE date = (SELECT MAX(date) FROM stats.options_terms)
        ORDER BY 1
