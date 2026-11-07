-- DISTINCT underlyings of the options universe (venue/target-type scoped)
-- — read from the terms table directly, NOT the v_options_quote view: the
-- view is a 7-way join over the hash-partitioned options_* tables, and a
-- DISTINCT over it forces the full join for a two-column answer (~1s per
-- venue on the CFFEX data volume). terms alone carries
-- (underlying_code, underlying_name, exchange, underlying_target_type).
-- SZSE/SSE ETF options keep native ETF codes; CFFEX index options use
-- index codes — the venues separate by underlying_target_type/exchange.
-- Serves GET /api/options/underlyings.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (starts with the fixed "underlying_code IS NOT NULL AND
--     underlying_code != ''")
    SELECT DISTINCT underlying_code, underlying_name
    FROM stats.options_terms
    WHERE {{where_clause}}
    ORDER BY underlying_code
