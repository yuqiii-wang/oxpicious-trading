-- Service Monitor — last successful exit of ONE pipeline service
-- (monitor.service_audit is_normal_exit), input to the freq auto-skip
-- evaluation (daily/monthly/semiannually/once windows).
-- Serves GET /api/monitor/stream/run/guard + the POST /stream/run guard.
-- args:
--   service: value text — the dotted module path of the pipeline service
SELECT max(ended_at) AS last_success_at
     FROM monitor.service_audit
     WHERE service = {{service}} AND is_normal_exit AND ended_at IS NOT NULL