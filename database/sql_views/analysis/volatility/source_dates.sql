-- Volatility pipeline (analysis.volatility) — the dates the run's codes
-- actually have daily bars for, in the sec_type's own basic-stats table
-- (stats.{etf,index,stock}_basic_stats — passed as a fragment arg). The
-- incremental missing-date set is computed HOST-side as this set minus
-- the target table's dates — scoped to the run's codes (NOT a whole
-- identity-table calendar: dates covered only by out-of-scope codes
-- would never clear and break the nightly no-op).
-- args: bars_table: fragment — SEC_TYPE_SOURCES[sec_type].bars_table
--       codes: value text[] — the run's universe
    SELECT DISTINCT ibs.date
    FROM {{bars_table}} ibs
    WHERE ibs.code = ANY({{codes}}::text[])
    ORDER BY ibs.date
