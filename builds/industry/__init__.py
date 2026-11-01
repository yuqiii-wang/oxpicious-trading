"""builds.industry — industry baseline build (stats.industry_basic_stats).

Migrated from the former analysis/industry_sentiments (2026-08-24;
renamed analysis/market_beta): the per-industry
BASELINE aggregation is a stats-level table (mirroring builds.index /
builds.stock), while the downstream analysis steps (correlations,
attributions, etf_contribution, hypes_and_drains) remain in
analysis.market_beta and read from stats.industry_basic_stats.
"""
