-- ============================================================================
--  Live Schema
--  Creates the `live` schema and grants access mirroring the analysis schema
--  conventions defined in database/init/00_create_db_and_user.sql.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS live;

-- ----------------------------------------------------------------------------
-- Grants: live schema
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA live TO public;
GRANT USAGE ON SCHEMA live TO anon;
GRANT USAGE ON SCHEMA live TO authenticated;
GRANT USAGE ON SCHEMA live TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA live GRANT ALL ON TABLES TO public;
ALTER DEFAULT PRIVILEGES IN SCHEMA live GRANT ALL ON SEQUENCES TO public;

ALTER DEFAULT PRIVILEGES IN SCHEMA live GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA live GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA live GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA live GRANT USAGE, SELECT ON SEQUENCES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA live GRANT USAGE, SELECT ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA live GRANT ALL ON SEQUENCES TO service_role;

-- Ensure postgres has full privileges on any existing objects
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA live TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA live TO postgres;

-- Add live to the postgres search path
ALTER ROLE postgres SET search_path TO stats, analysis, live, public;
