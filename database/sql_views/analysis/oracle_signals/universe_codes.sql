-- Oracle pipeline (analysis.oracle_signals) — the data-driven run
-- universe: every code with close data in the sec_type's own
-- basic-stats table (the etf / stock path; the index path uses the
-- config's fixed STUDY_INDEX_CODES and never reads this view).
-- args: bars_table: fragment — SEC_TYPE_SOURCES[sec_type].bars_table
    SELECT DISTINCT ibs.code
    FROM {{bars_table}} ibs
    WHERE ibs.close IS NOT NULL
    ORDER BY ibs.code
