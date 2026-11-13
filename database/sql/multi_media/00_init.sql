-- ============================================================================
--  Master Import Script — multi_media/ (regenerated 2026-11-10: ONE TABLE = ONE
--  FILE; complete + FK-safe order; supersedes the stale hand list).
--  Usage: psql -d analytics -f multi_media/00_init.sql
-- ============================================================================

-- schema files
\ir 00_schema.sql

-- tables (FK order)
\ir src_images.sql

