-- External Resources — raw audit rows behind ONE (service, date) bucket,
-- all status buckets (the no-filter variant of the endpoint's optional
-- ?status= narrowing).
-- Serves GET /api/monitor/ext-resources/audit.
-- args:
--   service: value text — the pipeline service that issued the requests
--   date: value date — the audit day (YYYY-MM-DD)
--   limit: value int — max rows (clamped 1-500)
SELECT id, resource_type, sub_type, url, status_code, is_failed,
              failure_reason, duration_ms, created_at
       FROM monitor.external_resource_audit
       WHERE service = {{service}} AND date = {{date}}::date
       ORDER BY created_at DESC, id DESC
       LIMIT {{limit}}