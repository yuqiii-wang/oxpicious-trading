-- Service Config — Server cluster view: the resident FastAPI area
-- servers' heartbeat rows (service = server_cluster.<area>, the PK).
-- Liveness vs the 240s stale threshold, the stop record and live
-- parallelism; the area→port map itself comes from
-- server_cluster/cluster_config.json (read by the route).
-- Serves GET /api/config/cluster.
-- args: none
SELECT h.service, h.pid, h.started_at, h.beat_at,
              EXTRACT(EPOCH FROM (now() - h.beat_at))::int AS beat_age_s,
              h.scope, h.sub_scope, h.stop_reason, h.is_normal_exit,
              h.stopped_at, h.live_parallel
       FROM monitor.service_heartbeat h
       WHERE starts_with(h.service, 'server_cluster.')
       ORDER BY h.service
