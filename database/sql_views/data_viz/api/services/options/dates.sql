-- DISTINCT trading dates + row/contract counters (date picker, caption).
-- COUNT(*) only — the (contract_code, date) PK makes every (date,
-- contract_code) pair unique, so the per-date DISTINCT contract count IS
-- the row count; COUNT(DISTINCT contract_code) instead forces a full-input
-- sort of the scope's rows (399k for the biggest CFFEX underlying) that
-- spilled ~11MB to external merge, ~2s — the HashAggregate plan this
-- admits runs the same scope in ~30ms (measured 2026-10-17). Serves
-- GET /api/options/dates.
-- args:
--   where_clause: fragment — conditional scope WHERE (termsWhere result
--     incl. the WHERE keyword) or ""
      SELECT t.date, COUNT(*) AS row_count, COUNT(*) AS contract_count
      FROM stats.options_terms t
      {{where_clause}}
      GROUP BY t.date
      ORDER BY t.date ASC
