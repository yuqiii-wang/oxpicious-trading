-- Service Monitor — every heartbeat row (resident always-on services AND
-- batch pipelines): last-run state with beat age in seconds, stop record,
-- live parallelism, AND the child-REPORTED progress (2026-11-21 control
-- unification Phase 4): the phase machine + run state, smallint label
-- codes decoded through the index (static.label_dim scalar subquery on
-- (domain, code) — never a CASE; NULL = the service never reported).
-- The derived (missing-set oracle) progress is NOT here: it is computed
-- from the data tables at the OWNING servers and reaches the UI through
-- the fleet fold (GET /api/monitor/fleet).
-- Serves GET /api/monitor/stream.
-- args: none
SELECT h.service, h.pid, h.started_at, h.beat_at,
              EXTRACT(EPOCH FROM (now() - h.beat_at))::int AS beat_age_s,
              h.scope, h.sub_scope, h.stop_reason, h.is_normal_exit,
              h.stopped_at, h.live_parallel,
              h.progress_phase, h.progress_state, h.progress_at,
              EXTRACT(EPOCH FROM (now() - h.progress_at))::int
                AS progress_age_s,
              (SELECT d.label FROM static.label_dim d
                WHERE d.domain = 'progress_phase'
                  AND d.code = h.progress_phase) AS progress_phase_label,
              (SELECT d.label FROM static.label_dim d
                WHERE d.domain = 'progress_state'
                  AND d.code = h.progress_state) AS progress_state_label
       FROM monitor.service_heartbeat h
       ORDER BY h.service
