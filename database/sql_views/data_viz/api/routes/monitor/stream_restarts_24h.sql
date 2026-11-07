-- Service Monitor — 24h watchdog-restart count per service (restarts24h
-- badge on the always-on stream rows).
-- Serves GET /api/monitor/stream.
-- args: none
SELECT service, count(*) AS restarts_24h
       FROM monitor.service_audit
       WHERE exit_reason LIKE 'watchdog restart%'
         AND ended_at > now() - interval '24 hours'
       GROUP BY service