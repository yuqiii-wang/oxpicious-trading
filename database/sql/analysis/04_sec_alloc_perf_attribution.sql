-- ============================================================================
--  RETIRED (2026-09-04): analysis.sec_alloc_perf_attribution
--
--  The pair-grain (index/index) cross-security logic this table hosted has
--  been MIGRATED to stats.cross_stats (sec_type='index') — see
--  database/sql/stats/14_cross_stats.sql, built by builds.cross_stats
--  (incremental / --force / --corr). All consumers were migrated:
--
--    • analyze.industry_sentiments.__main__     → builds.cross_stats.runner.run_cross_stats
--    • attributions (broad-market weights)      → stats.cross_stats sec_type='industry'
--    • etf_contribution (ETF amounts)           → stats.cross_stats sec_type='index'
--    • data_viz perf-attr endpoints             → stats.cross_stats sec_type='index'
--    • data_viz intraday-movements / member-idx → stats.cross_stats sec_type='index'
--
--  This file is now a CLEANUP script: re-running the SQL suite drops the
--  legacy table + dates map + its analysis_identity registration.
-- ============================================================================

DROP TABLE IF EXISTS analysis.sec_alloc_perf_attribution CASCADE;
DROP TABLE IF EXISTS analysis.sec_alloc_perf_attribution_dates CASCADE;

DELETE FROM analysis.analysis_identity WHERE name = 'sec_alloc_perf_attribution';
