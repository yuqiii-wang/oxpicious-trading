-- Service Monitor — EVERY pipeline run of ONE Shanghai-local day across
-- ALL services (monitor.service_audit, oldest first): the History mode's
-- day drill-down list under the daily run-count curve (same columns as
-- stream_runs.sql — the per-service expansion's run table). Includes the
-- resident processes' lives; a restart day shows one closing and one
-- opening life.
-- Serves GET /api/monitor/stream/runs/day.
-- args:
--   day: value text — the Shanghai-local day to list (YYYY-MM-DD,
--        regex-checked at the route)
--   limit: value int — max rows (clamped 1-2000 at the route)
SELECT id, service, scope, sub_scope, pid, started_at, ended_at,
              exit_reason, is_normal_exit
       FROM monitor.service_audit
       WHERE (started_at AT TIME ZONE 'Asia/Shanghai')::date = {{day}}::date
       ORDER BY started_at
       LIMIT {{limit}}