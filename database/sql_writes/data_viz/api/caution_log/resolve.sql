-- SQL write action — monitor.caution_log UPDATE (resolve one row).
-- Executed by data_viz/api/services/caution-log.service.ts (resolveCaution,
-- the Cautions tab's Resolve action, POST /api/monitor/cautions/resolve):
-- stamps is_resolved + resolved_timestamp on ONE open row. Never
-- un-resolves: a resolved row is history — a recurrence re-raises as a
-- NEW row through the upsert.
--
-- Params (node-postgres $n, in order):
--   $1 id BIGINT — the caution row to resolve
UPDATE monitor.caution_log
SET is_resolved = TRUE,
    resolved_timestamp = now()
WHERE id = $1::bigint
  AND NOT is_resolved
RETURNING id
