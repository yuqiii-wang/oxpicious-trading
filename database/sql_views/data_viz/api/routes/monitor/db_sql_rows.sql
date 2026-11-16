-- DB tab — the SQL audit panel: monitor.sql_audit rows (one per DISTINCT
-- statement, keyed by sha256 of the normalized text), ordered by a
-- whitelisted expression and optionally narrowed to a text substring or a
-- hash prefix. Serves GET /api/monitor/db/sql.
-- args:
--   q: value text — '' matches everything; else sql_text ILIKE %q% OR
--      sql_hash prefix match (the UI search box)
--   order_expr: fragment — a ROUTE-WHITELISTED ORDER BY expression
--      (total_exec_ms DESC / call_count DESC / last_active_at DESC /
--      avg_exec_ms DESC); never user text verbatim
--   limit: value int — max rows (clamped 1-500)
SELECT sql_hash::text, sql_text, queryid, roles, instances,
       call_count, total_exec_ms, avg_exec_ms, max_exec_ms, rows_total,
       first_seen_at, last_active_at
FROM monitor.sql_audit
WHERE sql_text ILIKE '%' || {{q}}::text || '%'
   OR sql_hash LIKE {{q}}::text || '%'
ORDER BY {{order_expr}}
LIMIT {{limit}}
