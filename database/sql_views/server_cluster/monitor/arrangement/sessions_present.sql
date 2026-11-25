-- Orchestration arranger data pre-check — which of the EXPECTED sessions
-- are present in one service's data table (a session is present when
-- the table holds any row dated with it; the caller diffs the expected
-- list against this — the absent sessions are the service's charge).
-- Serves server_cluster/monitor/pre_check.py (DataPreChecker — the
-- nightly chain's run / don't-run gate for a service with a declared
-- pre-check, a table with no exchange / code-slice filter).
-- args:
--   table_name: fragment — the schema-qualified data table (the caller
--                validates the config value against the table-name
--                charset)
--   date_column: fragment — the table's session-date column (an
--                identifier, the same validation)
--   sessions: value date[] — the expected sessions (trading days,
--             newest last)
SELECT d AS session
  FROM unnest({{sessions}}::date[]) AS d
 WHERE EXISTS (SELECT 1
                 FROM {{table_name}} t
                WHERE t.{{date_column}} = d)
