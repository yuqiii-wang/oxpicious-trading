-- DB tab — the instance vitals panel: the LATEST monitor.db_health row
-- per instance (primary + replica), decoded at the API boundary
-- (instance -> db_instance, replication_state -> db_replication_state).
-- Serves GET /api/monitor/db/instances (the `latest` half).
SELECT DISTINCT ON (instance)
       instance, checked_at, healthy, connect_ms, error, uptime_s,
       active_connections, db_size_bytes, cache_hit_ratio,
       replication_state, replay_backlog_bytes, wal_dir_bytes,
       cpu_pct, mem_pct
FROM monitor.db_health
ORDER BY instance, checked_at DESC
