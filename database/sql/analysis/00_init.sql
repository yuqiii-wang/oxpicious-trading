-- ============================================================================
--  Master Import Script — analysis/ (regenerated 2026-11-10: ONE TABLE = ONE
--  FILE; complete + FK-safe order; supersedes the stale hand list).
--  Usage: psql -d analytics -f analysis/00_init.sql
-- ============================================================================

\ir ../00_partition_utils.sql
-- schema files
\ir 01_analysis_schema.sql
\ir analysis_composites/00_schema.sql
\ir analysis_signals/00_schema.sql

-- tables (FK order)
\ir static/analysis_insight_metrics/insight_templates.sql
\ir static/analysis_insights/insight_templates.sql
\ir analysis_identity.sql
\ir analysis_signals/analysis_cache.sql
\ir analysis_signals/history_portfolio_signal_performance.sql
\ir analysis_signals/history_signal_performance.sql
\ir analysis_signals/history_signals.sql
\ir analysis_signals/portfolio_history_signals.sql
\ir analysis_signals/portfolio_signal_rules.sql
\ir analysis_signals/signal_amplification_history.sql
\ir analysis_signals/signal_amplification_rules.sql
\ir analysis_signals/signal_rules.sql
\ir futures_ext.sql
\ir industry_correlations.sql
\ir industry_hypes_seasonal.sql
\ir market_crowdedness.sql
\ir market_crowdedness_hot_sets.sql
\ir market_crowdedness_hot_tech.sql
\ir market_crowdedness_year.sql
\ir market_participation.sql
\ir mov_ave_price_vs_amt.sql
\ir mov_ave_spreads/mov_ave_high_low_pct.sql
\ir mov_ave_spreads/mov_ave_high_low_pct_streaks.sql
\ir mov_ave_spreads/mov_ave_momentum.sql
\ir mov_ave_spreads/mov_ave_rsi.sql
\ir mov_ave_spreads/mov_ave_rsi_ranks.sql
\ir mov_ave_spreads/mov_ave_spreads_codes.sql
\ir mov_ave_spreads/mov_ave_spreads_detail.sql
\ir mov_ave_spreads/mov_ave_spreads_detail_ema.sql
\ir mov_ave_spreads/mov_ave_spreads_detail_ohlc_gaps.sql
\ir mov_ave_spreads/mov_ave_spreads_detail_trailing.sql
\ir mov_ave_spreads/mov_ave_spreads_roof_and_floor_lines.sql
\ir mov_ave_spreads/mov_ave_spreads_roof_floor_estimates.sql
\ir mov_ave_spreads/mov_ave_spreads_roof_floor_rebounds.sql
\ir mov_ave_spreads/mov_ave_trading_amt.sql
\ir mov_ave_spreads/mov_ave_trading_amt_ratios.sql
\ir ohlcv_ext.sql
\ir options_expiry_identity.sql
\ir options_moneyness_series.sql
\ir options_oi_stats.sql
\ir pe.sql
\ir pe_pct.sql
\ir pe_pct_streaks.sql
\ir static/analysis_insight_metrics/insight_page_docs.sql
\ir trends/trend_days.sql
\ir trends/trend_episode_days.sql
\ir volatility.sql
\ir analysis_composites/industry_corr_benchmark_offsets.sql
\ir dividend_deliveries.sql
\ir dividend_est.sql
\ir dividends.sql
\ir exchange_margin_summary.sql
\ir futures_gap_quintiles.sql
\ir industry_attributions.sql
\ir industry_etf_contribution.sql
\ir industry_hypes_and_drains.sql
\ir industry_member_index_map.sql
\ir industry_stock_change_dist.sql
\ir margin_changes.sql
\ir margin_index_series.sql
\ir margin_industry_stats.sql
\ir margin_tech_stats.sql
\ir mov_ave_rsi_holiday.sql
\ir options_bands.sql
\ir options_iv_skew_stats.sql
\ir options_skewness_stats.sql
\ir options_spot_daily.sql
\ir options_vol_index.sql
\ir options_vol_index_vs_spot_price_corr.sql
\ir options_walls.sql
\ir pair_pct_change_ratios.sql
\ir pe_codes.sql

-- seeds
\ir static/analysis_insight_metrics/02_insight_templates_commons.sql
\ir static/analysis_insight_metrics/03_insight_templates_derivatives.sql
\ir static/analysis_insight_metrics/04_insight_templates_composites.sql
\ir static/analysis_insight_metrics/05_insight_templates_signals.sql
\ir static/analysis_insights/02_insight_templates_commons.sql
\ir static/analysis_insights/03_insight_templates_derivatives.sql
\ir static/analysis_insights/04_insight_templates_composites.sql
\ir static/analysis_insights/05_insight_templates_signals.sql

-- indexes
\ir 20_nav_code_lookup_indexes.sql
\ir 21_margin_trends_indexes.sql

