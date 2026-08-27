-- ============================================================================
--  Analysis Schema — Master Import Script
--  Execute all split SQL files in order.
--  Usage: psql -d "oxpicious-stats" -f analysis/00_init.sql
-- ============================================================================

\ir ../00_partition_utils.sql
\ir 01_analysis_schema.sql
\ir 02_analysis_identity.sql
\ir 03_mov_ave_spreads.sql
\ir 04_sec_alloc_perf_attribution.sql
\ir 05_industry_sentiments.sql
\ir 06_industry_member_index_map.sql
\ir 07_basic_analysis.sql
\ir 08_industry_hypes_and_drains.sql
\ir 09_industry_hypes_seasonal.sql
\ir 10_intraday_industry_sentiments.sql
\ir 11_pe_and_dividends.sql
