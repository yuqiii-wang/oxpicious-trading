-- ============================================================================
--  Maintenance Schema
--  Creates the `maintenance` schema: the server-cluster maintainer area's
--  audit tables (server_cluster/maintainer/) — one row per maintenance
--  batch the daily pass runs, mirroring the monitor schema's append-only
--  audit conventions (monitor.service_audit / monitor.external_resource_audit).
--
--  Tables:
--    maintenance.src_data_backup — append-only per-batch audit of the
--        source-data mirror (temps/ -> the stats-db backup drive): one
--        row per robocopy batch with what it copied (files / bytes),
--        when it ran, how long it took, and whether it completed.
--        Written by server_cluster/maintainer/jobs.py StatsDbBackupJob;
--        the latest OK batch's started_at is the next batch's
--        incremental cutoff (robocopy /MAXAGE).
--
--  Consumers:
--    server_cluster/maintainer/jobs.py — writer + the cutoff read
--    (monitoring UI surface may follow; no data_viz route today)
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS maintenance;

-- ----------------------------------------------------------------------------
-- Grants: maintenance schema
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA maintenance TO public;
GRANT USAGE ON SCHEMA maintenance TO anon;
GRANT USAGE ON SCHEMA maintenance TO authenticated;
GRANT USAGE ON SCHEMA maintenance TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA maintenance GRANT ALL ON TABLES TO public;
ALTER DEFAULT PRIVILEGES IN SCHEMA maintenance GRANT ALL ON SEQUENCES TO public;

ALTER DEFAULT PRIVILEGES IN SCHEMA maintenance GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA maintenance GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA maintenance GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA maintenance GRANT USAGE, SELECT ON SEQUENCES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA maintenance GRANT USAGE, SELECT ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA maintenance GRANT ALL ON SEQUENCES TO service_role;

-- Ensure postgres has full privileges on any existing objects
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA maintenance TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA maintenance TO postgres;
