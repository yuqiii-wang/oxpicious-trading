-- Service Monitor — currently live pipeline services (heartbeat row with
-- no stop record and a beat inside the staleness window), evaluated by the
-- cross-service parallel guard's included/excluded_parallel_services lists.
-- Serves GET /api/monitor/stream/run/guard + the POST /stream/run guard.
-- args:
--   service: value text — the candidate service (excluded from the set)
--   stale_after_s: value int — beat staleness window in seconds (STALE_AFTER_S)
SELECT service FROM monitor.service_heartbeat
       WHERE service <> {{service}} AND stop_reason IS NULL
         AND beat_at > now() - make_interval(secs => {{stale_after_s}})