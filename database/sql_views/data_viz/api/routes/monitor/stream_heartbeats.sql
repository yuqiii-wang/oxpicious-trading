-- Service Monitor — every heartbeat row (always-on stream services AND
-- batch pipelines): last-run state with beat age in seconds, stop record
-- and live parallelism. The always-on catalog annotates its rows and the
-- rest become the pipeline-process table.
-- Serves GET /api/monitor/stream.
-- args: none
SELECT h.service, h.pid, h.started_at, h.beat_at,
              EXTRACT(EPOCH FROM (now() - h.beat_at))::int AS beat_age_s,
              h.scope, h.sub_scope, h.stop_reason, h.is_normal_exit,
              h.stopped_at, h.live_parallel
       FROM monitor.service_heartbeat h
       ORDER BY h.service