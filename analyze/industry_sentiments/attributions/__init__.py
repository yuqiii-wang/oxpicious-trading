"""Industry-attribution step of analyze.industry_sentiments.

Industry-level composition overlap between each industry (as a group of
member indices) and each benchmark index. TWO classes of benchmarks are
materialized so the per-industry attribution bar chart shows both the
broad-market benchmarks AND the industry's own member indices.

Populates analysis.industry_attributions with one row per
(date, industry_id, benchmark_code). Benchmarks come from two sources:

  (A) BROAD-MARKET benchmarks (from analysis.sec_alloc_perf_attribution):

    industry_shared_weight  = SUM(code_sec_shared_weight) across the member
                              indices in the industry, sourced from
                              analysis.sec_alloc_perf_attribution (sec_type
                              ='index'). Each member contributes its OWN
                              weight on stocks shared with the benchmark, so
                              the sum is a clean "total member overlap" (can
                              exceed 1.0 — expected when summing multiple
                              member portfolios). Self-pairs (member ==
                              benchmark) are already excluded by
                              sec_alloc_perf_attribution.

    benchmark_shared_weight = benchmark's weight on the UNION of stocks held
                              by ANY industry member (latest sec_composition
                              snapshot). Each stock counted ONCE (union), so
                              no double-counting even when multiple members
                              hold the same stock. In percent (0-100),
                              bounded [0, 100]. Recomputed from
                              sec_composition (NOT summed from
                              sec_alloc_perf_attribution) because a naive
                              SUM of benchmark_sec_shared_weight across
                              members would double-count stocks held by
                              multiple members.

  (B) MEMBER-INDEX benchmarks (computed directly from sec_composition):

    For each industry, ALL of its non-broad member indices are also
    inserted as benchmarks. industry_shared_weight = SUM over other
    same-industry members N (N != M) of N's weight on stocks shared with
    the member index M. benchmark_shared_weight = M's weight on the
    industry stock union (typically ~100 since M is fully contained in its
    own industry). This is computed directly from sec_composition because
    sec_alloc_perf_attribution only keeps the top-3 non-broad indices per
    industry as benchmarks — not enough for "all member indices".

    Broad-market codes are EXCLUDED from member-index rows (already
    materialized by the broad-market INSERT). The non_this_industry_*
    columns stay NULL for these rows (the return-based decomposition is
    only meaningful for broad-market benchmarks).

HYBRID AGGREGATION RATIONALE
  industry_shared_weight is a clean SUM (each member contributes a DISTINCT
  portfolio's weight, so summing is valid). benchmark_shared_weight is NOT
  a clean SUM — the benchmark's weight on a stock is the SAME regardless of
  which member we pair it with, so a stock held by N members would have its
  benchmark weight counted N times. Recomputing from the union of industry
  member stocks avoids this.

IMPLEMENTATION
  The aggregation is pure SQL, so the whole transform runs server-side as
  INSERT ... SELECT statements (one for broad-market benchmarks, one for
  member-index benchmarks).

  BOTH modes use the MERGED broad-market INSERT — a single INSERT...SELECT
  that computes the weights AND the non_this_industry_* columns in one
  CTE pass (no separate UPDATE step, no per-industry loop):
    Force mode:       TRUNCATE + plain INSERT (full history), inside a
                      transaction so an interrupted run rolls back whole.
    Incremental mode: date-filtered INSERT (``sa.date = ANY($1::date[])``),
                      plain INSERT (no ON CONFLICT — target dates are
                      pruned to dates genuinely absent from the table, and
                      the whole step runs in one transaction), with the
                      stock/benchmark history scans capped at
                      LOOKBACK_TRADING_DAYS (510) broad-benchmark trading
                      days before the earliest target date (500 window
                      rows + LAG row + grid margin, plus a 45-calendar-day
                      per-stock LAG suspension margin), the heavy chain
                      pruned to the (industry, benchmark) pairs that
                      actually occur at the target dates (needed_pairs),
                      and the liquidity join split out of the warm-up
                      path (trading_amount has no rolling window — target
                      dates only). See sql_broad_market.py.

  Target-date pruning: incremental target dates are intersected with
  find_missing_attribution_dates inside run_attributions, so already
  covered dates (e.g. --with-corr window-end dates that the table already
  has) are skipped instead of conflicting.

DEPENDENCY
  The broad-market INSERT reads analysis.sec_alloc_perf_attribution, which
  is populated by analyze.sec_alloc_perf_attribution. If that table is
  empty (the upstream analysis has not been run), the broad-market INSERT
  produces no rows but the member-index INSERT still runs (it reads
  sec_composition directly). The step exits gracefully only if
  sec_alloc_perf_attribution is empty AND there are no member indices.

This package is an INTERNAL step of analyze.industry_sentiments — it is
invoked from __main__.py after the sentiments + correlations steps,
reusing the same DB connection. It is NOT a standalone runnable.

Module layout
  config            — table names, identity metadata, rolling windows,
                      incremental lookback cap, index DDL, session tuning.
  sql_broad_market  — shared composition CTEs + merged broad-market INSERT.
  sql_member_index  — member-index map populate + per-date expansion SQL.
  sql_equal         — equal-variant copy INSERT.
  queries           — guard/preview counts, rolling-backfill detection,
                      missing-date detection, incremental lookback
                      resolution.
  runner            — run_attributions orchestration.
"""
from analyze.industry_sentiments.attributions.config import (
    ANALYSIS_DESCRIPTION,
    ANALYSIS_NAME,
    ANALYZE_TABLE_SQL,
    LOOKBACK_EXTRA_CALENDAR_DAYS,
    LOOKBACK_TRADING_DAYS,
    MAP_TABLE,
    ROLLING_WINDOWS,
    SET_MAINTENANCE_WORK_MEM_SQL,
    SET_WORK_MEM_SQL,
    TABLE,
    _CREATE_SECONDARY_INDEX_DDL,
    _SECONDARY_INDEXES,
)
from analyze.industry_sentiments.attributions.queries import (
    COUNT_MEMBER_INDICES_SQL,
    COUNT_SOURCE_SQL,
    PREVIEW_DIMENSIONS_SQL,
    PREVIEW_MEMBER_DIMENSIONS_SQL,
    fetch_incremental_lookback_date,
    find_missing_attribution_dates,
    needs_rolling_backfill,
)
from analyze.industry_sentiments.attributions.runner import (
    run_attributions,
)
from analyze.industry_sentiments.attributions.sql_broad_market import (
    MERGED_BROAD_MARKET_INSERT_SQL_FULL,
    MERGED_BROAD_MARKET_INSERT_SQL_INCREMENTAL,
    _build_merged_broad_market_insert_sql,
    _format_composition_ctes,
    _rolling_price_expr,
)
from analyze.industry_sentiments.attributions.sql_equal import (
    EQUAL_INSERT_SQL_FULL,
    EQUAL_INSERT_SQL_INCREMENTAL,
    _build_equal_insert_sql,
)
from analyze.industry_sentiments.attributions.sql_member_index import (
    MEMBER_INDEX_INSERT_SQL_FULL,
    MEMBER_INDEX_INSERT_SQL_INCREMENTAL,
    MEMBER_INDEX_MAP_POPULATE_SQL,
    _build_member_index_insert_sql,
    _build_member_index_map_sql,
)

__all__ = [
    # config
    "TABLE",
    "ANALYSIS_NAME",
    "ANALYSIS_DESCRIPTION",
    "MAP_TABLE",
    "ROLLING_WINDOWS",
    "LOOKBACK_TRADING_DAYS",
    "LOOKBACK_EXTRA_CALENDAR_DAYS",
    "SET_WORK_MEM_SQL",
    "SET_MAINTENANCE_WORK_MEM_SQL",
    "ANALYZE_TABLE_SQL",
    # queries
    "COUNT_SOURCE_SQL",
    "COUNT_MEMBER_INDICES_SQL",
    "PREVIEW_DIMENSIONS_SQL",
    "PREVIEW_MEMBER_DIMENSIONS_SQL",
    "needs_rolling_backfill",
    "find_missing_attribution_dates",
    "fetch_incremental_lookback_date",
    # runner
    "run_attributions",
    # broad-market SQL
    "MERGED_BROAD_MARKET_INSERT_SQL_FULL",
    "MERGED_BROAD_MARKET_INSERT_SQL_INCREMENTAL",
    "_build_merged_broad_market_insert_sql",
    "_format_composition_ctes",
    "_rolling_price_expr",
    # member-index SQL
    "MEMBER_INDEX_MAP_POPULATE_SQL",
    "MEMBER_INDEX_INSERT_SQL_FULL",
    "MEMBER_INDEX_INSERT_SQL_INCREMENTAL",
    "_build_member_index_map_sql",
    "_build_member_index_insert_sql",
    # equal SQL
    "EQUAL_INSERT_SQL_FULL",
    "EQUAL_INSERT_SQL_INCREMENTAL",
    "_build_equal_insert_sql",
]
