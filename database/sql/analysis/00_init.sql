-- ============================================================================
--  Analysis Schema — Master Import Script
--  Execute all split SQL files in order.
--  Usage: psql -d "oxpicious-stats" -f analysis/00_init.sql
--
--  FRESH-INIT ONLY: this list contains destructive-refresh files
--  (DROP TABLE + CREATE, e.g. 05/08/09/12/13) — NEVER re-run it against a
--  populated database. Ongoing migrations go through the individual files
--  (all idempotent); the init scripts are only for a new container volume.
-- ============================================================================

\ir ../00_partition_utils.sql
\ir 01_analysis_schema.sql
\ir 02_analysis_identity.sql
\ir mov_ave_spreads/01_mov_ave_spreads_detail.sql
\ir mov_ave_spreads/02_mov_ave_spreads_detail_ema.sql
\ir mov_ave_spreads/03_mov_ave_spreads_detail_ohlc.sql
\ir mov_ave_spreads/04_mov_ave_rsi.sql
\ir mov_ave_spreads/05_mov_ave_trading_amt.sql
\ir mov_ave_spreads/06_mov_ave_trading_amt_ratios.sql
\ir mov_ave_spreads/07_mov_ave_high_low_pct.sql
\ir mov_ave_spreads/08_mov_ave_high_low_pct_streaks.sql
\ir 05_industry_sentiments.sql
\ir 06_industry_member_index_map.sql
\ir 08_industry_hypes_and_drains.sql
\ir 09_industry_hypes_seasonal.sql
\ir 11_pe_and_dividends.sql
\ir 12_margin.sql
\ir 13_margin_changes.sql
\ir 15_futures.sql
\ir 16_options.sql
\ir 17_non_trading_day_risk.sql
\ir 19_price_vs_amt.sql
-- Analysis Commons: forecast buckets + signal strategies + composites.
-- (02/03 of analysis_signals are $1-parameterized runtime files, applied by
-- the pipeline after every run — not standalone-runnable, excluded here.)
\ir analysis_composites/00_schema.sql
\ir analysis_composites/01_industry_corr_benchmark_offsets.sql
\ir analysis_forecasts/00_schema.sql
\ir analysis_forecasts/01_forecast_results.sql
\ir analysis_forecasts/02_mov_rsi_mov_std.sql
\ir analysis_forecasts/04_base_rates.sql
\ir analysis_forecasts/05_px_vol_state.sql
\ir analysis_forecasts/06_margin_ratio.sql
\ir analysis_forecasts/07_opp_pair_state.sql
\ir analysis_forecasts/08_mov_pairs.sql
\ir analysis_forecasts/09_mov_pairs_ema.sql
\ir analysis_forecasts/10_high_low_streaks.sql
\ir analysis_forecasts/11_pe_state.sql
\ir analysis_forecasts/12_dividend_state.sql
\ir analysis_signals/00_schema.sql
\ir analysis_signals/01_signals.sql
