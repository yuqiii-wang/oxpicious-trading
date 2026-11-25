-- Orchestration arranger data pre-check — which of the EXPECTED sessions
-- are present in one CODE SLICE of a service's data table, in ONE pass
-- (the slice is the service's own universe inside a table other
-- services share: one exchange's rows of stats.stock_basic_stats —
-- codes carry the .SZ / .SS / .BJ suffix — or of stats.etf_basic_stats;
-- a session another slice populated must not count as present here).
-- The single-scan twin of sessions_present_code_slice.sql. Serves
-- server_cluster/monitor/pre_check.py (DataPreChecker — the nightly
-- chain's run / don't-run gate, a code_scope filter).
-- args:
--   table_name: fragment — the schema-qualified data table (the caller
--                validates the config value against the table-name
--                charset)
--   date_column: fragment — the table's session-date column (an
--                identifier, the same validation)
--   sessions: value date[] — the expected sessions (trading days,
--             newest last)
--   code_scope: value text — POSIX regex the slice's codes match
--   code_column: fragment — the table's code column (identifier)
SELECT DISTINCT t.{{date_column}} AS session
  FROM {{table_name}} t
 WHERE t.{{date_column}} = ANY({{sessions}}::date[])
   AND t.{{code_column}} ~ {{code_scope}}::text
