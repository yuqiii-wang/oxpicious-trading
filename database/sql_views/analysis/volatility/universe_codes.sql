-- Volatility pipeline (analysis.volatility) — the data-driven universe
-- for a sec_type: every code with bars in the sec_type's own
-- basic-stats table (stats.{etf,stock}_basic_stats — passed as a
-- fragment arg). The index sec_type overrides this with the fixed
-- STUDY_INDEX_CODES study scope in the pipeline config; etf / stock
-- resolve here at run time.
-- args: bars_table: fragment — SEC_TYPE_SOURCES[sec_type].bars_table
    SELECT DISTINCT ibs.code
    FROM {{bars_table}} ibs
    ORDER BY ibs.code
