"""Configuration constants for the industry-attributions step.

Table names, analysis-identity metadata, rolling-window definitions, the
incremental warm-up bound, and force-mode index DDL. Pure constants — no
SQL building, no I/O.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
#  Tables + analysis identity
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_attributions"
ANALYSIS_NAME = "industry_attributions"
ANALYSIS_DESCRIPTION = (
    "Composition overlap between each industry (group of member indices) "
    "and each benchmark index. One row per (date, industry_id, "
    "benchmark_code, attribution_type). TWO attribution variants are "
    "materialized: 'trading_amt' (industry_shared_weight = SUM across "
    "member indices, can exceed 100) and 'equal' (industry_shared_weight "
    "= AVG = SUM / N, N = active member index count). "
    "benchmark_shared_weight is UNDIVIDED (same for both variants), so "
    "all non_this_industry_* columns are identical between variants. "
    "TWO benchmark classes are materialized: "
    "(1) BROAD-MARKET benchmarks — HYBRID aggregation: "
    "industry_shared_weight = SUM(code_sec_shared_weight) across member "
    "indices from stats.cross_stats sec_type='index' rows (own-weight on "
    "shared stocks in percent, can exceed 100, self-pairs excluded); "
    "benchmark_shared_weight = benchmark weight on the UNION of industry "
    "member stocks from stats.sec_composition (latest snapshot, bounded "
    "[0, 100] (percent), no double-counting, recomputed from compositions "
    "to avoid double-counting stocks held by multiple members). "
    "(2) MEMBER-INDEX benchmarks — each industry's OWN member indices are "
    "also inserted as benchmarks (computed directly from sec_composition, "
    "NOT from the cross_stats pair grain which only keeps top-3 per "
    "industry). industry_shared_weight = SUM over other same-industry "
    "members N of N's weight on stocks shared with the member index M; "
    "benchmark_shared_weight = M's weight on the industry stock union "
    "(typically ~100 since M is fully contained in its own industry). "
    "Broad-market codes are excluded from member-index rows (already "
    "materialized as broad-market benchmarks). Both classes use LATEST "
    "snapshot for all dates (weight_pct is stored as a percent, not a "
    "fraction). Built by analyze.industry_sentiments.attributions "
    "(internal step, truncate-then-recompute via server-side "
    "INSERT...SELECT). Depends on stats.cross_stats (sec_type='index') "
    "being populated first."
)

# Mapping table for the member-index benchmark rows (two-phase insert:
# phase 1 populates composition-derived weights ONCE, phase 2 expands to
# per-date rows).
MAP_TABLE = "analysis.industry_member_index_map"


# ---------------------------------------------------------------------------
#  Rolling windows
# ---------------------------------------------------------------------------

# Rolling windows (in trading days). Each window W produces a column
# benchmark_non_this_industry_rolling_{W}days_price computed as
# 100 × exp(sum(ln(1+r))) over ROWS BETWEEN (W-1) PRECEDING AND CURRENT ROW.
# 120d (~6 months) is the UI DEFAULT for the BenchmarkPriceChart shade
# overlay AND for analysis.industry_hypes_and_drains. Added in tandem with
# the industry_hypes_and_drains feature.
ROLLING_WINDOWS = [5, 20, 60, 120, 255, 500]

# Incremental lookback cap for the merged broad-market INSERT (B-A5
# candidate, implemented 2026-08-30): TRADING-DAY-PRECISE history bound
# replacing the former 800-calendar-day interval (~545 trading days).
#
# The longest rolling window (500 trading days) needs 500 prior window
# rows per (industry, benchmark) partition plus one LAG(close) row;
# 510 adds grid margin for benchmark suspension gaps. The bound is
# resolved by fetch_incremental_lookback_date() (queries.py) from the
# broad benchmarks' own trading calendar and passed as a plain $2::date
# parameter (plan-time constant — a scalar subquery was measured to be
# re-evaluated per outer row under the nested-loop plan this query's
# tiny CTE estimates produce). LOOKBACK_EXTRA_CALENDAR_DAYS shifts the
# resolved date further back so a stock's LAG(close) at the boundary
# row survives suspensions up to ~45 extra calendar days (the same
# slack class the former 800-day bound provided).
#
# The heavy CTEs are AS MATERIALIZED so they are computed ONCE and
# hash-joined, never re-scanned per outer row.
LOOKBACK_TRADING_DAYS = 510
LOOKBACK_EXTRA_CALENDAR_DAYS = 45


# ---------------------------------------------------------------------------
#  Session tuning + force-mode index management
# ---------------------------------------------------------------------------

# Bump work_mem for the big hash aggregate so the GROUP BY on ~25M source
# rows doesn't spill to disk excessively. Session-scoped (restored on
# reconnect; this step owns the connection for its duration).
SET_WORK_MEM_SQL = "SET work_mem = '512MB'"

# Bump maintenance_work_mem for CREATE INDEX (512MB lets the index builds
# use a large in-memory sort instead of spilling to disk). Session-scoped
# (the connection is in autocommit mode, so SET LOCAL would not persist
# past the single SET statement).
SET_MAINTENANCE_WORK_MEM_SQL = "SET maintenance_work_mem = '512MB'"

# Secondary indexes on analysis.industry_attributions. In force mode these
# are DROPPED before the bulk INSERT and RECREATED after, so the INSERT
# (with the non_this_industry computation merged into it) doesn't pay index
# maintenance. The PK (industry_id, benchmark_code, date, attribution_type)
# is KEPT for dedup safety. Only force mode drops/recreates; incremental
# mode (ON CONFLICT DO UPDATE) needs the indexes for the upsert.
#
# PK (industry_id, benchmark_code, date, attribution_type) serves the most
# common pattern: WHERE industry_id = ... AND benchmark_code = ...
# The single secondary index serves benchmark-first queries.
_SECONDARY_INDEXES = [
    "idx_industry_attributions_bench_date_industry",
]

# DDL to recreate the secondary indexes (must match database/sql/analysis/
# 05_industry_sentiments.sql exactly).
_CREATE_SECONDARY_INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_industry_attributions_bench_date_industry "
    "ON analysis.industry_attributions (benchmark_code, date, industry_id)",
]

ANALYZE_TABLE_SQL = "ANALYZE analysis.industry_attributions"
