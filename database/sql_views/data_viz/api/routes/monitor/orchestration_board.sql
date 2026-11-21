-- Service Monitor / Orchestration page — the arrangement board: every
-- orchestration-tier service's arrangement row
-- (monitor.orchestration_task_state, state decoded through the label
-- index) LEFT JOINed to its live heartbeat (the running section), its
-- non-daily completeness state (the queue's missing/attempts tooltip)
-- and a success-since-arranged flag (a due service that already exited
-- normally after the arrangement demotes to the no-need section). The
-- route classifies the three sections host-side.
-- Serves GET /api/monitor/orchestration/board.
-- args: none
SELECT s.service, s.tier, s.step, s.freq, s.priority, s.state,
       l.label AS state_label, s.last_success_at, s.due_at,
       s.arranged_at, s.updated_at,
       hb.pid, hb.started_at AS hb_started_at, hb.beat_at,
       GREATEST(0, extract(epoch FROM (now() - hb.beat_at)))::int
         AS beat_age_s,
       nd.state AS nd_state_code,
       ndl.label AS nd_state_label,
       nd.missing_count, nd.attempts, nd.next_check_at,
       EXISTS (SELECT 1 FROM monitor.service_audit a
                WHERE a.service = s.service AND a.is_normal_exit
                  AND a.ended_at IS NOT NULL
                  AND a.ended_at > s.arranged_at) AS success_since_arranged
  FROM monitor.orchestration_task_state s
  LEFT JOIN static.label_dim l
         ON l.domain = 'orchestration_plan_state' AND l.code = s.state
  LEFT JOIN monitor.service_heartbeat hb
         ON hb.service = s.service AND hb.stop_reason IS NULL
        AND hb.beat_at > now() - make_interval(secs => 240)
  LEFT JOIN monitor.non_daily_task_state nd
         ON nd.service = s.service
  LEFT JOIN static.label_dim ndl
         ON ndl.domain = 'non_daily_state' AND ndl.code = nd.state
 ORDER BY s.priority DESC, s.service
