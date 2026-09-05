"""builds.cross_stats — THE canonical cross-security stats producer.

Populates stats.cross_stats (+ stats.cross_stats_dates):
  • PAIR grain (sec_type='index'): (code, benchmark_code) index pairs —
    composition shared weights, ETF-market liquidity (+ ratio MA5),
    optional stride-20 grid rolling correlations. Migrated from
    analyze.sec_alloc_perf_attribution (2026-09-04).
  • INDUSTRY grain (sec_type='industry'): industry_id vs broad-market
    benchmarks — union-overlap weights + benchmark/shared/non-shared
    trading-amount split. Migrated from the broad-market half of
    analyze.industry_sentiments.attributions (2026-09-04).

Consumers read stats.cross_stats directly (all former
analysis.sec_alloc_perf_attribution consumers migrated 2026-09-04).

MODULE LAYOUT
  config.py            tables, constants, description
  fetch.py             DB fetch primitives (weights, closes, ETF amounts)
  _perf.py             perf-blocker logging helpers
  compute/             pair-grain pandas/cudf pipeline (GPU-first)
  _industry.py         industry-grain SQL builder (INSERT...SELECT)
  runner.py            orchestration (dates, force, corr sub-command)
  __main__.py          CLI (--force / --corr), composition preflight gate
"""
