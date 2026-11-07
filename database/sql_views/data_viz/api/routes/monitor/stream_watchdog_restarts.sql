-- Service Monitor — the watchdog restart trail (recentRestarts): the dead
-- life's monitor.service_audit rows closed with a
-- 'watchdog restart: …' exit_reason.
-- Serves GET /api/monitor/stream.
-- args: none
SELECT service, ended_at, pid, exit_reason, scope, sub_scope
       FROM monitor.service_audit
       WHERE exit_reason LIKE 'watchdog restart%'
       ORDER BY ended_at DESC
       LIMIT 20