-- The sec_type's option underlyings (DISTINCT underlying_code over
-- stats.options_terms by underlying_target_type) — the options families'
-- universe before the family exclusions (options_wall drops the SSE ETF
-- underlyings) and the active-code intersection. Empty for sec_types
-- without options (stock).
-- args:
--   target_type: value text — stats.options_terms.underlying_target_type
--     for the signals sec_type ("INDEX" / "ETF")
        SELECT DISTINCT underlying_code
        FROM stats.options_terms
        WHERE underlying_target_type = {{target_type}}
