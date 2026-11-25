-- Orchestration arranger data pre-check — which of the EXPECTED sessions
-- a per-code sweep has LOADED: the session counts as present only when
-- the code slice holds at least min_codes distinct codes for it (any-row
-- presence is too weak here — a sweep that died after its first codes
-- leaves rows for the session while most of its universe is still
-- missing, and other sources share the table). The CSI price sweep is
-- the case: its 264 CSI-native codes (93x / 95x / H families) against a
-- floor. Serves server_cluster/monitor/pre_check.py (DataPreChecker —
-- the nightly chain's run / don't-run gate, a slice with min_codes).
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
--   min_codes: value integer — the session is present only when at
--              least this many distinct codes of the slice hold it
SELECT t.{{date_column}} AS session
  FROM {{table_name}} t
 WHERE t.{{date_column}} = ANY({{sessions}}::date[])
   AND t.{{code_column}} ~ {{code_scope}}::text
 GROUP BY t.{{date_column}}
HAVING count(DISTINCT t.{{code_column}}) >= {{min_codes}}::int
