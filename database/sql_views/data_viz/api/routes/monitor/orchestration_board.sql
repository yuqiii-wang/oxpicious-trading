-- Service Monitor / Orchestration page — the arrangement board: every
-- orchestration-tier service's arrangement row
-- (monitor.orchestration_task_state, state decoded through the label
-- index) LEFT JOINed to its live heartbeat (the running section), its
-- non-daily completeness state (the queue's missing/attempts tooltip)
-- and a success-since-arranged flag (a due service that already exited
-- normally after the arrangement demotes to the no-need section). The
-- route classifies the three sections host-side.
-- Each row also carries the service's p95 run duration (the duration 95%
-- of its last 20 completed normal-exit runs finished within — the run
-- history's outlier-robust "how long does it take" stat, NULL when the
-- service has no such run yet) and the DATA PRE-CHECK reading behind the
-- verdict (basis decoded through the label index + the absent-session
-- count and lists — pc_ prefixed: the non-daily join carries its own
-- missing_count).
-- Serves GET /api/monitor/orchestration/board.
-- args: none
SELECT s.service, s.tier, s.step, s.freq, s.priority, s.state,
       l.label AS state_label, s.last_success_at, s.due_at,
       s.arranged_at, s.updated_at,
       s.basis, bl.label AS pc_basis_label, s.basis AS pc_basis_code,
       s.missing_count AS pc_missing_count,
       s.missing_detail AS pc_missing_detail,
       hb.pid, hb.started_at AS hb_started_at, hb.beat_at,
       GREATEST(0, extract(epoch FROM (now() - hb.beat_at)))::int
         AS beat_age_s,
       nd.state AS nd_state_code,
       ndl.label AS nd_state_label,
       nd.missing_count, nd.attempts, nd.next_check_at,
       dur.p95_run_s,
       EXISTS (SELECT 1 FROM monitor.service_audit a
                WHERE a.service = s.service AND a.is_normal_exit
                  AND a.ended_at IS NOT NULL
                  AND a.ended_at > s.arranged_at) AS success_since_arranged
  FROM monitor.orchestration_task_state s
  LEFT JOIN LATERAL (
         SELECT percentile_cont(0.95) WITHIN GROUP (
                  ORDER BY extract(epoch FROM (r.ended_at - r.started_at)))
                  AS p95_run_s
           FROM (SELECT r2.started_at, r2.ended_at
                   FROM monitor.service_audit r2
                  WHERE r2.service = s.service AND r2.is_normal_exit
                    AND r2.ended_at IS NOT NULL
                  ORDER BY r2.started_at DESC
                  LIMIT 20) r
       ) dur ON TRUE
  LEFT JOIN static.label_dim l
         ON l.domain = 'orchestration_plan_state' AND l.code = s.state
  LEFT JOIN static.label_dim bl
         ON bl.domain = 'orchestration_plan_basis' AND bl.code = s.basis
  LEFT JOIN monitor.service_heartbeat hb
         ON hb.service = s.service AND hb.stop_reason IS NULL
        AND hb.beat_at > now() - make_interval(secs => 240)
  LEFT JOIN monitor.non_daily_task_state nd
         ON nd.service = s.service
  LEFT JOIN static.label_dim ndl
         ON ndl.domain = 'non_daily_state' AND ndl.code = nd.state
 ORDER BY s.priority DESC, s.service
