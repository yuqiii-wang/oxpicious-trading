-- Cautions tab — the cross-service caution trail (monitor.caution_log):
-- one OPEN row per issue per service (the dedupe contract), service
-- errors / AI suspicions / manual pending bugs. Serves
-- GET /api/monitor/cautions.
-- args:
--   state: value text — 'open' | 'resolved' | 'all' (the working-set default is 'open')
--   source: value int — caution_source code filter, NULL = any
--   severity: value int — caution_severity code filter, NULL = any
--   limit: value int — max rows (clamped 1-500)
--   source_label: fragment — labelDecodeSql('caution_source', ...) decode (API boundary)
--   severity_label: fragment — labelDecodeSql('caution_severity', ...) decode (API boundary)
SELECT id, service, source,
       {{source_label}} AS source_label,
       severity_level,
       {{severity_label}} AS severity_label,
       title, detail, dedupe_key, occurrence_count,
       is_resolved, created_at, last_seen_at, resolved_timestamp
FROM monitor.caution_log
WHERE ({{state}}::text = 'all'
       OR ({{state}}::text = 'open') = (NOT is_resolved))
  AND ({{source}}::smallint IS NULL OR source = {{source}}::smallint)
  AND ({{severity}}::smallint IS NULL OR severity_level = {{severity}}::smallint)
ORDER BY is_resolved,                -- open rows first (false sorts first)
         severity_level DESC,        -- worst first
         last_seen_at DESC,          -- still-live issues on top
         id DESC
LIMIT {{limit}}
