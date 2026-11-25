-- Orchestration arranger data pre-check — which of the EXPECTED sessions
-- are present in one EXCHANGE SLICE of a service's data table (the
-- slice is the service's own universe inside a table other services
-- share: one exchange's rows of the SHARED stats.options_terms — SSE /
-- SZSE / CFFEX; a session another exchange populated must not count as
-- present here). Serves server_cluster/monitor/pre_check.py
-- (DataPreChecker — the nightly chain's run / don't-run gate, an
-- exchange filter, optionally narrowed by a code_scope).
-- args:
--   table_name: fragment — the schema-qualified data table (the caller
--                validates the config value against the table-name
--                charset)
--   date_column: fragment — the table's session-date column (an
--                identifier, the same validation)
--   sessions: value date[] — the expected sessions (trading days,
--             newest last)
--   exchange: value text — the exchange the slice is scoped to
--   exchange_column: fragment — the table's exchange column (identifier)
--   code_scope: value text — POSIX regex narrowing the slice's codes
--               further; NULL = the whole exchange
--   code_column: fragment — the table's code column (identifier)
SELECT d AS session
  FROM unnest({{sessions}}::date[]) AS d
 WHERE EXISTS (SELECT 1
                 FROM {{table_name}} t
                WHERE t.{{date_column}} = d
                  AND t.{{exchange_column}} = {{exchange}}::text
                  AND ({{code_scope}}::text IS NULL
                       OR t.{{code_column}} ~ {{code_scope}}::text))
