-- ============================================================================
--  Master Import Script — monitoring/ (regenerated 2026-11-10: ONE TABLE = ONE
--  FILE; complete + FK-safe order; supersedes the stale hand list).
--  Usage: psql -d analytics -f monitoring/00_init.sql
-- ============================================================================

-- schema files
\ir 00_monitor_schema.sql

-- tables (FK order)
\ir db_health.sql
\ir external_resource_audit.sql
\ir external_resource_stats.sql
\ir service_audit.sql
\ir service_heartbeat.sql
\ir src_data_audit.sql

