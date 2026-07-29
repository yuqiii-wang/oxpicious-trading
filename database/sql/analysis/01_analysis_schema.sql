-- ============================================================================
--  Analysis Schema
--  Creates the `analysis` schema and grants access mirroring the stats schema
--  conventions defined in database/init/00_create_db_and_user.sql.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS analysis;

-- ----------------------------------------------------------------------------
-- Grants: analysis schema
-- ----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA analysis TO public;
GRANT USAGE ON SCHEMA analysis TO anon;
GRANT USAGE ON SCHEMA analysis TO authenticated;
GRANT USAGE ON SCHEMA analysis TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA analysis GRANT ALL ON TABLES TO public;
ALTER DEFAULT PRIVILEGES IN SCHEMA analysis GRANT ALL ON SEQUENCES TO public;

ALTER DEFAULT PRIVILEGES IN SCHEMA analysis GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA analysis GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA analysis GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA analysis GRANT USAGE, SELECT ON SEQUENCES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA analysis GRANT USAGE, SELECT ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA analysis GRANT ALL ON SEQUENCES TO service_role;

-- Ensure postgres has full privileges on any existing objects
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA analysis TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA analysis TO postgres;

-- Add analysis to the postgres search path (after stats, public)
ALTER ROLE postgres SET search_path TO stats, analysis, public;
