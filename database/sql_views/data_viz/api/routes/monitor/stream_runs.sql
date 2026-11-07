-- Service Monitor — run audit history of ONE pipeline process
-- (monitor.service_audit, newest first, per-service limit). Backs the
-- pipeline table's expandable run-history rows.
-- Serves GET /api/monitor/stream/runs.
-- args:
--   service: value text — the dotted module path of the pipeline service
--   limit: value int — max rows (clamped 1-100 at the route)
SELECT id, service, scope, sub_scope, pid, started_at, ended_at,
              exit_reason, is_normal_exit
       FROM monitor.service_audit
       WHERE service = {{service}}
       ORDER BY started_at DESC
       LIMIT {{limit}}