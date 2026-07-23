-- Supabase init script - runs on container startup via /docker-entrypoint-initdb.d
-- Consolidated from: create_db.sql + migrations/000000_init_supabase.sql

-- ============================================================================
-- Create application database (idempotent)
-- ============================================================================
SELECT 'CREATE DATABASE "oxpicious-stats"'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'oxpicious-stats')\gexec

-- Switch to the application database
\c "oxpicious-stats"

-- ============================================================================
-- Extensions
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- Supabase roles (create if they don't exist)
-- ============================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN;
    END IF;
END $$;

-- ============================================================================
-- Create stats schema
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS stats;

-- ============================================================================
-- Grants: public schema
-- ============================================================================
GRANT USAGE ON SCHEMA public TO anon;
GRANT USAGE ON SCHEMA public TO authenticated;
GRANT USAGE ON SCHEMA public TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO service_role;

-- ============================================================================
-- Grants: stats schema
-- ============================================================================
GRANT USAGE ON SCHEMA stats TO public;
GRANT USAGE ON SCHEMA stats TO anon;
GRANT USAGE ON SCHEMA stats TO authenticated;
GRANT USAGE ON SCHEMA stats TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA stats GRANT ALL ON TABLES TO public;
ALTER DEFAULT PRIVILEGES IN SCHEMA stats GRANT ALL ON SEQUENCES TO public;

ALTER DEFAULT PRIVILEGES IN SCHEMA stats GRANT SELECT ON TABLES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA stats GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA stats GRANT ALL ON TABLES TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA stats GRANT USAGE, SELECT ON SEQUENCES TO anon;
ALTER DEFAULT PRIVILEGES IN SCHEMA stats GRANT USAGE, SELECT ON SEQUENCES TO authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA stats GRANT ALL ON SEQUENCES TO service_role;

-- Grant privileges on existing objects in stats schema (if any)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA stats TO postgres;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA stats TO postgres;

-- Set search path to include stats schema
ALTER ROLE postgres SET search_path TO stats, public;
