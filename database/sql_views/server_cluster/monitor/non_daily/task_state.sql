-- Non-daily ops — the current per-task state rows
-- (monitor.non_daily_task_state), state decoded through the label
-- index. Serves server_cluster/monitor/non_daily.py (NonDailyOps
-- inventory + the backfill's due scan).
-- args: none
SELECT s.service, s.tier, s.state, l.label AS state_label,
       s.check_kind, s.missing_count, s.missing_detail, s.attempts,
       s.last_check_at, s.last_rerun_at, s.next_check_at, s.updated_at
  FROM monitor.non_daily_task_state s
  LEFT JOIN static.label_dim l
         ON l.domain = 'non_daily_state' AND l.code = s.state
 ORDER BY s.tier, s.service
