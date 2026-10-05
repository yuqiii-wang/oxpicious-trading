-- ============================================================================
--  Master Import Script
--  Execute all split SQL files in order.
--  Usage: psql -d analytics -f 00_init.sql
-- ============================================================================

\ir 01_debt.sql
\ir 02_etf_margin.sql
\ir 03_sec_composition.sql
\ir 04_options_quote.sql
\ir 05_index.sql
\ir 06_stock.sql
\ir 07_sec_classification.sql
\ir 08_index_exts.sql
\ir 09_stock_dividends.sql
\ir 10_stock_margin.sql
\ir 11_sec_info.sql
\ir 12_futures.sql
\ir 13_industry.sql
\ir 14_cross_stats.sql
\ir 15_cross_stats_code_summary.sql
\ir 16_options_intraday.sql
\ir 17_sec_board_map.sql
\ir 18_market_regimes.sql
\ir 19_sec_etf_links.sql
\ir 20_fund_nav.sql
\ir 21_label_dim.sql
\ir 98_intraday_retention.sql
\ir 99_reconstruct_views.sql