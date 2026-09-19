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
\ir 07_sec_classification.sql
\ir 08_index_exts.sql
\ir 09_stock_dividends.sql
\ir 10_stock_margin.sql
\ir 11_sec_info.sql
\ir 12_futures_baseline.sql
\ir 13_industry_baseline.sql
\ir 14_cross_stats.sql
\ir 15_cross_stats_code_summary.sql
\ir 16_mov_ave_market_hypes.sql
\ir 17_sec_board_map.sql
\ir 99_reconstruct_views.sql