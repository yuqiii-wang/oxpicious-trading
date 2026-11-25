-- Missed-fire guard read — the previous pass's start for ONE
-- maintainer audit service (monitor.service_audit). Serves
-- server_cluster.maintainer.jobs (MissedFireGuard.check — the
-- scheduled-job tripwire that cautions when the last pass predates the
-- job's previous scheduled fire, i.e. a fire found no live server and
-- was never caught up: the 2026-11-22 Sunday 07:00 pass). The CURRENT
-- pass's row is not yet written when this runs, so the returned row IS
-- the previous pass. Zero rows = first pass ever (nothing to compare).
-- args:
--   service: value text — the audit service name
--     (server_cluster.maintainer.bring_up | ...pipeline | ...daily_check)
SELECT MAX(started_at) AS last_started_at
FROM monitor.service_audit
WHERE service = {{service}}::text
