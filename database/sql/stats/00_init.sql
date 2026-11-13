-- ============================================================================
--  Master Import Script — stats/ (regenerated 2026-11-10: ONE TABLE = ONE
--  FILE; complete + FK-safe order; supersedes the stale hand list).
--  Usage: psql -d analytics -f stats/00_init.sql
-- ============================================================================

\ir ../00_partition_utils.sql
-- tables (FK order)
\ir cross_stats.sql
\ir cross_stats_benchmark_weight_summary.sql
\ir cross_stats_code_summary.sql
\ir cross_stats_dates.sql
\ir debt_identity.sql
\ir etf_identity.sql
\ir etf_intraday_5min.sql
\ir etf_trading_amt.sql
\ir fund_nav.sql
\ir futures_identity.sql
\ir index_identity.sql
\ir index_intraday_5min.sql
\ir industry_basic_stats.sql
\ir market_regime_spans.sql
\ir market_regimes.sql
\ir options_identity.sql
\ir options_intraday_5min.sql
\ir pboc_oma.sql
\ir sec_board_map.sql
\ir sec_classification.sql
\ir sec_composition.sql
\ir sec_etf_links.sql
\ir sec_importance_ranks.sql
\ir sec_index_tags.sql
\ir sec_info.sql
\ir sec_owners.sql
\ir sec_similars.sql
\ir stock_dividends.sql
\ir stock_identity.sql
\ir stock_intraday_5min.sql
\ir debt_lpr.sql
\ir debt_mlf.sql
\ir debt_omo.sql
\ir debt_outright_repo.sql
\ir debt_repo.sql
\ir debt_shibor.sql
\ir debt_treasury.sql
\ir etf_adjustment.sql
\ir etf_basic_stats.sql
\ir etf_liquidity_margin.sql
\ir etf_tech_stats.sql
\ir exchange_trading_amt.sql
\ir futures_basic_stats.sql
\ir index_basic_stats.sql
\ir index_exts.sql
\ir index_tech_stats.sql
\ir index_valuation.sql
\ir options_aggregate.sql
\ir options_greeks.sql
\ir options_settlement.sql
\ir options_strike.sql
\ir options_terms.sql
\ir options_volume_oi.sql
\ir sec_reports.sql
\ir stock_adjustment.sql
\ir stock_basic_stats.sql
\ir stock_liquidity_margin.sql
\ir stock_margin.sql
\ir stock_tech_stats.sql

-- views / maintenance
\ir 98_intraday_retention.sql
\ir 99_reconstruct_views.sql

