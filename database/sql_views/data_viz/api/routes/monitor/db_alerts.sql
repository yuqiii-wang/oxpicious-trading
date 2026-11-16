-- DB tab — the write-heavy alert trail (monitor.db_table_alert): the
-- flag STATE TRANSITIONS (raise / downgrade / clear) with severity +
-- evidence, newest first. Serves GET /api/monitor/db/alerts.
-- args:
--   limit: value int — max rows (clamped 1-500)
SELECT id, raised_at, table_schema, table_name, severity,
       write_rate, threshold_rate, window_s, message
FROM monitor.db_table_alert
ORDER BY raised_at DESC, id DESC
LIMIT {{limit}}
