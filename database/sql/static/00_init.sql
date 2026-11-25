-- ============================================================================
--  Master Import Script — static/ (regenerated 2026-11-10: ONE TABLE = ONE
--  FILE; complete + FK-safe order; supersedes the stale hand list).
--  Usage: psql -d analytics -f static/00_init.sql
-- ============================================================================

-- tables (FK order)
\ir docs/docs.sql
\ir keyword_maps/keyword_maps.sql
\ir label_dim/label_dim.sql
\ir fin_sentiment_dict/fin_sentiment_dict.sql
\ir portfolio_rules/portfolio_rules_docs.sql
\ir secrets/secret_config.sql
\ir signal_rules/signal_rules_docs.sql

-- seeds
\ir docs/static/10_ui_ma_spread.sql
\ir docs/static/20_ui_analysis_pages.sql
\ir docs/static/30_ui_derivatives_market.sql
\ir docs/static/40_ui_baselines_live_strategy.sql
\ir docs/static/50_ui_column_docs.sql
\ir keyword_maps/05_seed_skip.sql
\ir keyword_maps/06_seed_components.sql
\ir keyword_maps/statics/02_seed_industry.sql
\ir keyword_maps/statics/03_seed_market.sql
\ir keyword_maps/statics/04_seed_broadmarket.sql
\ir keyword_maps/statics/05_seed_market_expansion.sql
\ir keyword_maps/statics/06_seed_broadmarket_expansion.sql
\ir keyword_maps/statics/07_seed_industry_expansion.sql
\ir keyword_maps/statics/08_seed_english.sql
\ir keyword_maps/statics/09_seed_market_expansion2.sql
\ir keyword_maps/statics/10_seed_global_companies.sql
\ir fin_sentiment_dict/01_seed_negative.sql
\ir fin_sentiment_dict/02_seed_positive.sql
\ir fin_sentiment_dict/03_seed_both.sql
\ir label_dim/02_market_state.sql
\ir label_dim/03_industry_aggregation.sql
\ir label_dim/03_signal_detection.sql
\ir label_dim/04_security_identity.sql
\ir label_dim/05_trading.sql
\ir label_dim/06_monitoring_infra.sql
\ir label_dim/07_text_llm.sql
\ir label_dim/08_keyword_maps.sql
\ir portfolio_rules/01_neg_corr_hedge_pair.sql
\ir secrets/01_seed_secret_config.sql
\ir signal_rules/01_mov_rsi.sql
\ir signal_rules/02_bollinger.sql
\ir signal_rules/03_ma_cross.sql
\ir signal_rules/04_options_wall.sql
\ir signal_rules/05_options_vix.sql
\ir signal_rules/06_momentum.sql
\ir signal_rules/07_rf_break.sql
\ir signal_rules/08_hedge_pairs.sql
\ir signal_rules/09_rebound.sql
\ir signal_rules/10_macd.sql

