-- ============================================================================
--  Analysis Schema — Master Import Script
--  Execute all split SQL files in order.
--  Usage: psql -d "oxpicious-stats" -f analysis/00_init.sql
-- ============================================================================

\ir 01_analysis_schema.sql
\ir 02_analysis_identity.sql
\ir 03_mov_ave_spreads.sql
\ir 04_sec_alloc_perf_attribution.sql
