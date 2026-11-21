-- Orchestration arranger — the last successful exit per service
-- (monitor.service_audit normal exits), the arrangement rule's input.
-- One grouped pass over the audit trail; the arranger picks its tier
-- universe host-side (never a per-service query loop).
-- Serves server_cluster/monitor/arrangement.py (OrchestrationArranger.arrange).
-- args: none
SELECT service, max(ended_at) AS last_success_at
  FROM monitor.service_audit
 WHERE is_normal_exit AND ended_at IS NOT NULL
 GROUP BY service
