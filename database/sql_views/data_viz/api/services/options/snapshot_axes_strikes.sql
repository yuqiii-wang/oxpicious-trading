-- DISTINCT strikes across the scope's history — the wall panel's unified
-- strike axis. Cached per underlying/venue (never changes with the
-- selected date, so date switches don't re-run the full-history DISTINCT
-- joins). Runs under COMBINED_SESSION_SETS (work_mem + no parallel
-- workers). Serves GET /api/options/snapshot-axes.
-- args:
--   where_clause: fragment — conditional scope WHERE (termsWhere result
--     incl. the WHERE keyword) or ""
SELECT DISTINCT s.strike_price
         FROM stats.options_terms t
         JOIN stats.options_strike s ON s.contract_code = t.contract_code AND s.date = t.date
         {{where_clause}}
         ORDER BY 1
