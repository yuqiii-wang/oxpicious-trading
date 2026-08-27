"""builds.industry — industry baseline build (stats.industry_basic_stats).

Migrated from analyze/industry_sentiments (2026-08-24): the per-industry
BASELINE aggregation is a stats-level table (mirroring builds.index /
builds.stock), while the downstream analysis steps (correlations,
attributions, etf_contribution, hypes_and_drains) remain in
analyze.industry_sentiments and read from stats.industry_basic_stats.
"""
