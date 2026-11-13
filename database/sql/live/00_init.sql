-- ============================================================================
--  Master Import Script — live/ (regenerated 2026-11-10: ONE TABLE = ONE
--  FILE; complete + FK-safe order; supersedes the stale hand list).
--  Usage: psql -d analytics -f live/00_init.sql
-- ============================================================================

\ir ../00_partition_utils.sql
-- schema files
\ir 00_live_schema.sql

-- tables (FK order)
\ir live_identity.sql
\ir live_signals.sql
\ir options_intraday_oi.sql
\ir options_intraday_skewness.sql
\ir sec_alloc_live_attribution.sql

