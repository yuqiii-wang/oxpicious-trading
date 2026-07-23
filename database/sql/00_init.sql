-- ============================================================================
--  Master Import Script
--  Execute all split SQL files in order.
--  Usage: psql -d analytics -f 00_init.sql
-- ============================================================================

\ir 01_debt_baseline.sql
\ir 02_etf_margin.sql
\ir 03_sec_composition.sql
\ir 04_options_quote.sql
\ir 05_index_baseline.sql
\ir 06_stock_baseline.sql
\ir 07_etf_meta.sql
\ir 08_etf_composition.sql
\ir 09_stock_industry_map.sql
\ir 99_reconstruct_views.sql