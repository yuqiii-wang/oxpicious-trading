-- Orchestration arranger — the current per-service arrangement rows
-- (monitor.orchestration_task_state), state and basis decoded through
-- the label index. Serves server_cluster/monitor/arrangement.py (the
-- GET /arrangement inventory face).
-- args: none
SELECT s.service, s.tier, s.step, s.freq, s.priority, s.state,
       l.label AS state_label, s.last_success_at, s.due_at,
       s.arranged_at, s.updated_at,
       s.basis, bl.label AS basis_label, s.missing_count,
       s.missing_detail
  FROM monitor.orchestration_task_state s
  LEFT JOIN static.label_dim l
         ON l.domain = 'orchestration_plan_state' AND l.code = s.state
  LEFT JOIN static.label_dim bl
         ON bl.domain = 'orchestration_plan_basis' AND bl.code = s.basis
 ORDER BY s.tier, s.service
