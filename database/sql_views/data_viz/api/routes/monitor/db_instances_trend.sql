-- DB tab — the instance vitals panel's trailing trend: connections,
-- cache hit ratio and replay backlog per instance over a trailing
-- window (the db_health sampler writes ~1 row/instance/min, so the
-- window is bounded by construction). Serves GET /api/monitor/db/instances
-- (the `trend` half).
-- args:
--   hours: value int — trailing window in hours (clamped 0-168);
--      0 = ARCHIVE: no lower bound — the full retained history (the
--      db_health sampler's retention prunes the table, so the
--      predicate's OR-arm simply wins and every retained sample is
--      eligible)
SELECT instance, checked_at, active_connections, cache_hit_ratio,
       replay_backlog_bytes
FROM monitor.db_health
WHERE ({{hours}}::int <= 0 OR checked_at >= now() - make_interval(hours => {{hours}}::int))
ORDER BY instance, checked_at
