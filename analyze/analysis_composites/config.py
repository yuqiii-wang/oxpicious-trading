"""Configuration for analyze.analysis_composites (composite analyses)."""
from __future__ import annotations

# ---------------------------------------------------------------------------
#  Table / identity
# ---------------------------------------------------------------------------

TABLE_OFFSETS = "analysis_composites.industry_corr_benchmark_offsets"

ANALYSIS_NAME_OFFSETS = "industry_corr_benchmark_offsets"

# Mirrors the identity description in
# database/sql/analysis/analysis_composites/01_industry_corr_benchmark_offsets.sql.
ANALYSIS_DESCRIPTION_OFFSETS = (
    "Opposite industry correlations by benchmark offset: pairwise Pearson "
    "correlation of two industries' MA curves of mean_close "
    "(stats.industry_basic_stats) audited over 20/60/255 trading-day "
    "windows, RAW (overall_corr_ma{W}_{W}d) and after each industry's MA "
    "trend is offset by a broad-market benchmark — benchmark MA rebased to "
    "the industry's MA level at each window start (k = MA_X[s]/MA_B[s]) "
    "and SUBTRACTED, rebuilt as a recomputed price starting at 100 "
    "(offset_sub_corr_*; removes the common market factor so an industry "
    "up while the benchmark is up more is DOWN after the offset), plus the "
    "derived opposite_score_ma{W}_{W}d = (1 - offset_sub_corr)/2 in [0,1] "
    "(1 = perfectly opposite once the benchmark factor is removed). One "
    "row per (industry_id, benchmark_industry_id, pool_size, "
    "benchmark_code, start_date, interval); window grid identical to "
    "analysis.industry_correlations (stride 20). Default benchmark 000300. "
    "Sources: stats.industry_basic_stats.mean_close + "
    "stats.index_basic_stats.close. Built by python -m "
    "analyze.analysis_composites (incremental / --force / --industry "
    "filtered)."
)

# ---------------------------------------------------------------------------
#  Window grid — identical to analysis.industry_correlations
# ---------------------------------------------------------------------------

# Same-pool slices materialized (cross-pool comparisons intentionally not
# materialized — see analyze/industry_sentiments/correlations.py).
POOL_SIZES = ["small", "mid", "large", "all"]

# Window lengths in trading days: *_ma{W}_{W}d audits the W-day window
# starting on start_date over the W-day MA curves.
WINDOWS = [20, 60, 255]

# Stride in trading days between consecutive window starts on the pool
# calendar grid (stored as the `interval` column, default 20).
INTERVAL_DAYS = 20

# Minimum overlapping dates for a (pair, pool) to be materialized at all.
MIN_OVERLAP = min(WINDOWS)

# ---------------------------------------------------------------------------
#  Sources
# ---------------------------------------------------------------------------

# Industry composite close (trend source — same as correlations step).
BASELINE_TABLE = "stats.industry_basic_stats"

# Benchmark index closes (offset source).
BENCHMARK_TABLE = "stats.index_basic_stats"

# Broad-market offset benchmarks materialized by default. benchmark_code is
# part of the PK, so additional benchmarks can coexist (--benchmark).
DEFAULT_BENCHMARKS = ("000300",)
