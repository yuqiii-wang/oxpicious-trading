"""Internal attributions step for analyze.industry_sentiments.

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

  Force mode: TRUNCATE then INSERT...SELECT (full recompute).
  Incremental mode: INSERT...SELECT with a date filter
  (``sa.date = ANY($1::date[])``) + ON CONFLICT DO UPDATE (no truncate).

DEPENDENCY
  The broad-market INSERT reads analysis.sec_alloc_perf_attribution, which
  is populated by analyze.sec_alloc_perf_attribution. If that table is
  empty (the upstream analysis has not been run), the broad-market INSERT
  produces no rows but the member-index INSERT still runs (it reads
  sec_composition directly). The step exits gracefully only if
  sec_alloc_perf_attribution is empty AND there are no member indices.

This module is an INTERNAL step of analyze.industry_sentiments — it is
invoked from __main__.py after the sentiments + correlations steps,
reusing the same DB connection. It is NOT a standalone runnable.
"""
from __future__ import annotations

import datetime
import gc
import time
from typing import Optional, Set

from utils.build_commons import (
    truncate_table_async,
)
from analyze._common import upsert_analysis_identity


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_attributions"
ANALYSIS_NAME = "industry_attributions"
ANALYSIS_DESCRIPTION = (
    "Composition overlap between each industry (group of member indices) "
    "and each benchmark index. One row per (date, industry_id, "
    "benchmark_code). TWO benchmark classes are materialized: "
    "(1) BROAD-MARKET benchmarks — HYBRID aggregation: "
    "industry_shared_weight = SUM(code_sec_shared_weight) across member "
    "indices from analysis.sec_alloc_perf_attribution (own-weight on "
    "shared stocks in percent, can exceed 100, self-pairs excluded); "
    "benchmark_shared_weight = benchmark weight on the UNION of industry "
    "member stocks from stats.sec_composition (latest snapshot, bounded "
    "[0, 100] (percent), no double-counting, recomputed from compositions "
    "to avoid double-counting stocks held by multiple members). "
    "(2) MEMBER-INDEX benchmarks — each industry's OWN member indices are "
    "also inserted as benchmarks (computed directly from sec_composition, "
    "NOT from sec_alloc_perf_attribution which only keeps top-3 per "
    "industry). industry_shared_weight = SUM over other same-industry "
    "members N of N's weight on stocks shared with the member index M; "
    "benchmark_shared_weight = M's weight on the industry stock union "
    "(typically ~100 since M is fully contained in its own industry). "
    "Broad-market codes are excluded from member-index rows (already "
    "materialized as broad-market benchmarks). Both classes use LATEST "
    "snapshot for all dates (weight_pct is stored as a percent, not a "
    "fraction). Built by analyze.industry_sentiments.attributions "
    "(internal step, truncate-then-recompute via server-side "
    "INSERT...SELECT). Depends on analysis.sec_alloc_perf_attribution "
    "being populated first."
)


# ---------------------------------------------------------------------------
#  SQL
# ---------------------------------------------------------------------------

# Guard: bail out early if the upstream table is empty / missing.
COUNT_SOURCE_SQL = """
    SELECT COUNT(*) AS n
    FROM analysis.sec_alloc_perf_attribution
    WHERE sec_type = 'index'
"""

# Lightweight preview: distinct industries + benchmarks that will appear in
# the output (without running the full GROUP BY). Scans the source table
# once but avoids the expensive hash aggregate. Restricted to broad-market
# benchmarks — only these are materialized in industry_attributions.
PREVIEW_DIMENSIONS_SQL = """
    SELECT
        COUNT(DISTINCT cls.industry_id) AS n_industries,
        COUNT(DISTINCT sa.benchmark_code) AS n_benchmarks
    FROM analysis.sec_alloc_perf_attribution sa
    JOIN (
        SELECT DISTINCT code, industry_id
        FROM stats.sec_classification
        WHERE type = 'index'
          AND industry_id IS NOT NULL
          AND industry_id <> ''
    ) cls ON cls.code = sa.code
    WHERE sa.sec_type = 'index'
      AND sa.code_sec_shared_weight IS NOT NULL
      AND sa.benchmark_code IN (
          SELECT code FROM stats.sec_index_tags WHERE is_broad_market = TRUE
      )
"""

# The full transform, server-side.
#
# Composition CTEs — shared between the INSERT (weight columns) and the
# UPDATE (non-this-industry columns). Defined once to avoid duplication.
#
# CTE chain:
#   latest            — latest snapshot_date per index code (source_type
#                       ='index', stock_code NOT NULL).
#   holdings          — latest-snapshot holdings (code, stock_code,
#                       weight_pct) for indices.
#   industry_stocks   — DISTINCT (industry_id, stock_code) across all member
#                       indices (the UNION of stocks held by ANY industry
#                       member). Each stock counted once per industry.
#   benchmark_shared  — per (industry_id, benchmark_code): SUM(benchmark
#                       weight_pct) on the industry's union stocks.
#                       Constant across dates. Pairs with no overlap are
#                       absent (NULL after LEFT JOIN -> coerced to 0).
_COMPOSITION_CTES = """latest AS (
    SELECT code, MAX(snapshot_date) AS max_date
    FROM stats.sec_composition
    WHERE source_type = 'index'
      AND stock_code IS NOT NULL
    GROUP BY code
),
holdings AS (
    SELECT sc.code, sc.stock_code, sc.weight_pct
    FROM stats.sec_composition sc
    JOIN latest ld
        ON sc.code = ld.code AND sc.snapshot_date = ld.max_date
    WHERE sc.source_type = 'index'
      AND sc.stock_code IS NOT NULL
),
industry_stocks AS (
    SELECT DISTINCT cls.industry_id, h.stock_code
    FROM holdings h
    JOIN stats.sec_classification cls
        ON cls.code = h.code AND cls.type = 'index'
    WHERE cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      {industry_filter}
),
benchmark_shared AS (
    SELECT
        ist.industry_id,
        h.code AS benchmark_code,
        SUM(h.weight_pct) AS benchmark_shared_weight
    FROM industry_stocks ist
    JOIN holdings h ON h.stock_code = ist.stock_code
    GROUP BY ist.industry_id, h.code
)"""

# industry_shared CTE — per (industry_id, benchmark_code, date):
#   SUM(code_sec_shared_weight) across member indices, from
#   sec_alloc_perf_attribution. HAVING SUM IS NOT NULL drops pairs where
#   the benchmark has NO composition data (every member's shared weight is
#   NULL). Zero-overlap pairs (explicit 0) are kept.
#
# Final SELECT: industry_shared LEFT JOIN benchmark_shared, COALESCE(NULL->0).
# For rows surviving the HAVING filter the benchmark is guaranteed to have
# composition data, so a NULL benchmark_shared_weight means "benchmark has
# composition but holds none of the industry's union stocks" = zero overlap.
#
# Two variants:
#   _FULL: no date filter, used in force mode (TRUNCATE issued separately
#          first; plain INSERT without ON CONFLICT).
#   _INCREMENTAL: adds ``sa.date = ANY($1::date[])`` to the industry_shared
#          CTE + ON CONFLICT DO UPDATE so only target-date rows are
#          upserted without truncating the table.
_CTE_PREFIX = """
WITH {composition_ctes},
-- Broad-market benchmark codes — only these are materialized. Non-broad
-- benchmarks are skipped entirely (no rows inserted). The is_broad_market
-- flag comes from stats.sec_index_tags.
broad_codes AS (
    SELECT DISTINCT code
    FROM stats.sec_index_tags
    WHERE is_broad_market = TRUE
),
industry_shared AS (
    SELECT
        cls.industry_id,
        sa.benchmark_code,
        sa.date,
        SUM(sa.code_sec_shared_weight) AS industry_shared_weight
    FROM analysis.sec_alloc_perf_attribution sa
    JOIN (
        SELECT DISTINCT code, industry_id
        FROM stats.sec_classification cls
        WHERE cls.type = 'index'
          AND cls.industry_id IS NOT NULL
          AND cls.industry_id <> ''
          {industry_filter}
    ) cls ON cls.code = sa.code
    WHERE sa.sec_type = 'index'
      AND sa.benchmark_code IN (SELECT code FROM broad_codes)
    {date_filter}
    GROUP BY cls.industry_id, sa.benchmark_code, sa.date
    HAVING SUM(sa.code_sec_shared_weight) IS NOT NULL
)
INSERT INTO analysis.industry_attributions
    (industry_id, benchmark_code, date,
     industry_shared_weight, benchmark_shared_weight)
SELECT
    isw.industry_id,
    isw.benchmark_code,
    isw.date,
    ROUND(isw.industry_shared_weight, 4) AS industry_shared_weight,
    COALESCE(ROUND(bsw.benchmark_shared_weight, 4), 0) AS benchmark_shared_weight
FROM industry_shared isw
LEFT JOIN benchmark_shared bsw
    ON bsw.industry_id = isw.industry_id
   AND bsw.benchmark_code = isw.benchmark_code
"""


def _format_composition_ctes(industry_filter: str = "") -> str:
    """Format _COMPOSITION_CTES with the given industry_filter placeholder."""
    return _COMPOSITION_CTES.format(industry_filter=industry_filter)


def _build_broad_market_sql(
    date_filter: str = "",
    industry_filter: str = "",
    on_conflict: str = "",
) -> str:
    """Build a broad-market INSERT variant.

    Args:
      date_filter: e.g. "" for full, "AND sa.date = ANY($1::date[])" for
        incremental.
      industry_filter: e.g. "" for all industries, "AND cls.industry_id =
        $N::text" for per-industry.
      on_conflict: e.g. "" for plain INSERT (after TRUNCATE), or the ON
        CONFLICT DO UPDATE clause for incremental.
    """
    return _CTE_PREFIX.format(
        composition_ctes=_format_composition_ctes(industry_filter),
        date_filter=date_filter,
        industry_filter=industry_filter,
    ) + on_conflict


# Full recompute (all industries at once) — kept for backwards compat.
INSERT_SELECT_SQL_FULL = _build_broad_market_sql()

# Incremental (all industries at once) — kept for backwards compat.
INSERT_SELECT_SQL_INCREMENTAL = _build_broad_market_sql(
    date_filter="AND sa.date = ANY($1::date[])",
    on_conflict="""
ON CONFLICT (date, industry_id, benchmark_code) DO UPDATE SET
    industry_shared_weight  = EXCLUDED.industry_shared_weight,
    benchmark_shared_weight = EXCLUDED.benchmark_shared_weight
""",
)

# Per-industry variants — used by the memory-aware loop in run_attributions.
# Parameter $1 = industry_id (full) or $2 = industry_id (incremental, $1 = dates).
BROAD_MARKET_INSERT_PER_INDUSTRY_FULL = _build_broad_market_sql(
    industry_filter="AND cls.industry_id = $1::text"
)

BROAD_MARKET_INSERT_PER_INDUSTRY_INCREMENTAL = _build_broad_market_sql(
    date_filter="AND sa.date = ANY($1::date[])",
    industry_filter="AND cls.industry_id = $2::text",
    on_conflict="""
ON CONFLICT (date, industry_id, benchmark_code) DO UPDATE SET
    industry_shared_weight  = EXCLUDED.industry_shared_weight,
    benchmark_shared_weight = EXCLUDED.benchmark_shared_weight
""",
)

# Bump work_mem for the big hash aggregate so the GROUP BY on ~44M source
# rows doesn't spill to disk excessively. Session-scoped (restored on
# reconnect; this step owns the connection for its duration).
SET_WORK_MEM_SQL = "SET work_mem = '512MB'"

# ---------------------------------------------------------------------------
#  Non-this-industry price / rolling_Xdays_price / trading_amt computation.
#
#  Computed ONLY for broad-market benchmarks (sec_index_tags.is_broad_market).
#  For non-broad benchmarks the columns remain NULL.
#
#  Return-based decomposition:
#    shared_portfolio_return = SUM(weight × stock_return) / SUM(weight)
#      for stocks shared between the benchmark and the industry union.
#    non_industry_return = (bench_return - swf × shared_return) / (1 - swf)
#      where swf = benchmark_shared_weight / 100.
#    price (today)  = bench_prev_close × (1 + non_industry_return)
#    rolling_Xdays_price = 100 × exp(sum(ln(1 + non_industry_return))) over
#                      the trailing X-day window ending on `date`. NULL
#                      returns are treated as 0 so the cumprod carries
#                      forward; returns outside [-0.5, 0.5] also treated as 0
#                      to prevent compounding artifacts.
#    trading_amt    = bench.trading_amount - SUM(shared_stock.trading_amount)
#
#  The CTE chain reuses _COMPOSITION_CTES (latest/holdings/industry_stocks/
#  benchmark_shared) so the composition snapshot is computed once per
#  connection, then layers on stock + benchmark price data.
#
#  {date_filter} placeholder: in incremental mode, adds
#  ``AND ia.date = ANY($1::date[])`` to the UPDATE WHERE so only target-date
#  rows are touched (the CTEs still compute full history for the rolling
#  windows).
#
#  Rolling windows (in trading days). Each window W produces a column
#  benchmark_non_this_industry_rolling_{W}days_price computed as
#  100 × exp(sum(ln(1+r))) over ROWS BETWEEN (W-1) PRECEDING AND CURRENT ROW.
ROLLING_WINDOWS = [5, 20, 60, 255, 500]


def _rolling_price_expr(window: int) -> str:
    """Generate the SQL expression for one rolling X-day price column.

    Computes ``100 × exp(sum(ln(1+r)))`` over the trailing X-day window
    (ROWS BETWEEN (X-1) PRECEDING AND CURRENT ROW). Returns outside
    [-0.5, 0.5] are treated as 0 to prevent artifacts from compounding.
    NULL returns also treated as 0 so the cumprod carries forward.
    """
    return f"""        100.0 * exp(
            SUM(CASE
                WHEN nir.non_industry_return IS NOT NULL
                     AND nir.non_industry_return > -0.5
                     AND nir.non_industry_return <= 0.5
                THEN ln(1.0 + nir.non_industry_return)
                ELSE 0
            END) OVER (
                PARTITION BY nir.industry_id, nir.benchmark_code
                ORDER BY nir.date
                ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW
            )
        ) AS non_this_industry_rolling_{window}days_price"""


_NON_THIS_INDUSTRY_UPDATE_SQL = """
WITH {composition_ctes},
-- Broad-market benchmark codes (only these get the new columns)
broad_codes AS (
    SELECT DISTINCT code
    FROM stats.sec_index_tags
    WHERE is_broad_market = TRUE
),
-- Shared stocks per (industry, broad_benchmark) pair with benchmark weights
shared_stocks AS (
    SELECT DISTINCT
        ist.industry_id,
        h.code AS benchmark_code,
        h.stock_code,
        h.weight_pct
    FROM industry_stocks ist
    JOIN holdings h ON h.stock_code = ist.stock_code
    WHERE h.code IN (SELECT code FROM broad_codes)
),
-- Unique shared stock codes (fetch closes once per stock)
unique_stock_codes AS (
    SELECT DISTINCT stock_code FROM shared_stocks
),
-- Stock closes + returns (computed once per stock via WINDOW)
-- close comes from stock_basic_stats; trading_amount from stock_liquidity_margin
-- (mirrors etf_liquidity_margin split). LEFT JOIN liquidity_margin so stocks
-- with no liquidity row still get a close-based return (trading_amount NULL).
stock_daily AS (
    SELECT
        usc.stock_code,
        sbs.date,
        sbs.close,
        slm.trading_amount,
        LAG(sbs.close) OVER w AS prev_close
    FROM unique_stock_codes usc
    JOIN stats.stock_basic_stats sbs ON sbs.code = usc.stock_code
    LEFT JOIN stats.stock_liquidity_margin slm
        ON slm.date = sbs.date AND slm.code = sbs.code
    WHERE sbs.close IS NOT NULL
    WINDOW w AS (PARTITION BY usc.stock_code ORDER BY sbs.date)
),
stock_returns AS (
    SELECT
        stock_code,
        date,
        trading_amount,
        CASE
            WHEN prev_close IS NOT NULL AND prev_close != 0
            THEN (close - prev_close) / prev_close
            ELSE NULL
        END AS stock_return
    FROM stock_daily
),
-- Benchmark closes + returns (broad-market only)
bench_daily AS (
    SELECT
        code AS benchmark_code,
        date,
        close,
        trading_amount,
        LAG(close) OVER w AS prev_close
    FROM stats.index_basic_stats
    WHERE code IN (SELECT code FROM broad_codes)
      AND close IS NOT NULL
    WINDOW w AS (PARTITION BY code ORDER BY date)
),
bench_returns AS (
    SELECT
        benchmark_code,
        date,
        close,
        prev_close,
        trading_amount,
        CASE
            WHEN prev_close IS NOT NULL AND prev_close != 0
            THEN (close - prev_close) / prev_close
            ELSE NULL
        END AS bench_return
    FROM bench_daily
),
-- Shared portfolio return per (industry, benchmark, date).
-- Weighted avg of stock returns, normalized by weights of stocks WITH data.
shared_portfolio AS (
    SELECT
        ss.industry_id,
        ss.benchmark_code,
        sr.date,
        SUM(ss.weight_pct * sr.stock_return)
            / NULLIF(SUM(ss.weight_pct) FILTER (WHERE sr.stock_return IS NOT NULL), 0)
            AS shared_return,
        SUM(sr.trading_amount) AS shared_trading_amt
    FROM shared_stocks ss
    JOIN stock_returns sr ON sr.stock_code = ss.stock_code
    GROUP BY ss.industry_id, ss.benchmark_code, sr.date
),
-- Non-industry return: decompose benchmark return into shared + non-shared.
-- LEFT JOIN shared_portfolio so all benchmark dates are included even when
-- no shared stock data exists (non_industry_return falls back to bench_return).
non_industry_returns AS (
    SELECT
        br.benchmark_code,
        bsw.industry_id,
        br.date,
        br.prev_close AS bench_prev_close,
        br.trading_amount AS bench_trading_amt,
        sp.shared_return,
        sp.shared_trading_amt,
        bsw.benchmark_shared_weight,
        CASE
            WHEN br.prev_close IS NULL OR br.prev_close = 0 THEN NULL
            WHEN bsw.benchmark_shared_weight IS NULL THEN NULL
            -- Guard: when shared_weight >= 95%, the denominator (1 - swf)
            -- approaches 0 and the decomposition becomes numerically
            -- unstable (extreme returns). NULL out these rows.
            WHEN bsw.benchmark_shared_weight >= 95 THEN NULL
            WHEN bsw.benchmark_shared_weight = 0 OR sp.shared_return IS NULL THEN
                (br.close - br.prev_close) / br.prev_close
            ELSE
                (
                    (br.close - br.prev_close) / br.prev_close
                    - (bsw.benchmark_shared_weight / 100.0) * sp.shared_return
                ) / (1.0 - bsw.benchmark_shared_weight / 100.0)
        END AS non_industry_return
    FROM bench_returns br
    JOIN benchmark_shared bsw
        ON bsw.benchmark_code = br.benchmark_code
    LEFT JOIN shared_portfolio sp
        ON sp.benchmark_code = br.benchmark_code
       AND sp.industry_id = bsw.industry_id
       AND sp.date = br.date
),
-- Final computed values (window functions for rolling X-day cumprod).
-- Cap non_industry_return at [-0.5, 0.5] (±50%) to prevent numerical
-- instability from compounding in the rolling product. Daily returns
-- beyond ±50% on a broad-market index are almost certainly artifacts of
-- the return-based decomposition (small denominator when shared_weight
-- is high), not real market movements.
computed AS (
    SELECT
        nir.industry_id,
        nir.benchmark_code,
        nir.date,
        -- price (today): prev_close * (1 + capped_non_industry_return)
        CASE
            WHEN nir.bench_prev_close IS NOT NULL
                 AND nir.non_industry_return IS NOT NULL
                 AND abs(nir.non_industry_return) <= 0.5
            THEN nir.bench_prev_close * (1 + nir.non_industry_return)
            ELSE NULL
        END AS non_this_industry_price,
        -- rolling_Xdays_price columns (one per ROLLING_WINDOWS entry).
        -- Each is 100 × exp(sum(ln(1+r))) over the trailing X-day window.
{rolling_select},
        -- trading_amt: benchmark - shared
        CASE
            WHEN nir.bench_trading_amt IS NOT NULL
                 AND nir.shared_trading_amt IS NOT NULL
            THEN nir.bench_trading_amt - nir.shared_trading_amt
            ELSE NULL
        END AS non_this_industry_trading_amt
    FROM non_industry_returns nir
)
UPDATE analysis.industry_attributions ia
SET
    benchmark_non_this_industry_price = c.non_this_industry_price,
{rolling_set},
    benchmark_non_this_industry_trading_amt = c.non_this_industry_trading_amt
FROM computed c
WHERE ia.industry_id = c.industry_id
  AND ia.benchmark_code = c.benchmark_code
  AND ia.date = c.date
  {date_filter}
"""

def _build_non_this_industry_sql(
    date_filter: str = "",
    industry_filter: str = "",
) -> str:
    """Build a non-this-industry UPDATE variant.

    Args:
      date_filter: e.g. "" for full, "AND ia.date = ANY($1::date[])" for
        incremental.
      industry_filter: e.g. "" for all industries, "AND cls.industry_id =
        $N::text" for per-industry (applied to industry_stocks CTE).
    """
    rolling_select = ",\n        ".join(
        _rolling_price_expr(w) for w in ROLLING_WINDOWS
    )
    rolling_set = ",\n    ".join(
        f"    benchmark_non_this_industry_rolling_{w}days_price = c.non_this_industry_rolling_{w}days_price"
        for w in ROLLING_WINDOWS
    )
    return _NON_THIS_INDUSTRY_UPDATE_SQL.format(
        composition_ctes=_format_composition_ctes(industry_filter),
        date_filter=date_filter,
        rolling_select=rolling_select,
        rolling_set=rolling_set,
    )


# Full recompute (all industries at once) — kept for backwards compat.
NON_THIS_INDUSTRY_SQL_FULL = _build_non_this_industry_sql()

# Incremental (all industries at once) — kept for backwards compat.
NON_THIS_INDUSTRY_SQL_INCREMENTAL = _build_non_this_industry_sql(
    date_filter="AND ia.date = ANY($1::date[])"
)

# Per-industry variants — used by the memory-aware loop in run_attributions.
# Parameter $1 = industry_id (full) or $2 = industry_id (incremental, $1 = dates).
NON_THIS_INDUSTRY_SQL_PER_INDUSTRY_FULL = _build_non_this_industry_sql(
    industry_filter="AND cls.industry_id = $1::text"
)

NON_THIS_INDUSTRY_SQL_PER_INDUSTRY_INCREMENTAL = _build_non_this_industry_sql(
    date_filter="AND ia.date = ANY($1::date[])",
    industry_filter="AND cls.industry_id = $2::text",
)


# ---------------------------------------------------------------------------
#  Member-index benchmark rows.
#
#  In addition to the broad-market benchmarks materialized above, each
#  industry's OWN member indices are also inserted as benchmark rows so the
#  per-industry attribution bar chart shows the industry's own indices
#  alongside the broad-market benchmarks.
#
#  For each (industry_id, member_index M, date):
#    industry_shared_weight  = SUM over other same-industry members N
#                              (N != M) of N's weight on stocks held by
#                              BOTH N and M. Computed directly from
#                              stats.sec_composition (NOT from
#                              sec_alloc_perf_attribution, which only keeps
#                              the top-3 non-broad indices per industry as
#                              benchmarks — not enough for "all member
#                              indices"). Mirrors code_sec_shared_weight
#                              semantics (subject's OWN weight on shared
#                              stocks). Self-pair (M, M) excluded.
#    benchmark_shared_weight = M's weight on the UNION of industry member
#                              stocks (from the benchmark_shared CTE).
#                              Since M is itself a member of the industry,
#                              M's stocks are a subset of the union, so
#                              this is typically ~100 (M's total weight on
#                              its own stocks).
#
#  Broad-market codes are EXCLUDED from member_indices (they are already
#  materialized by the broad-market INSERT above and would cause PK
#  conflicts). This also means BROAD_* industries (whose members are all
#  broad-market) get NO member-index rows — correct, since those indices
#  are already covered as broad-market benchmarks.
#
#  The non_this_industry_* columns stay NULL for member-index rows (the
#  return-based decomposition is only meaningful for broad-market
#  benchmarks; _NON_THIS_INDUSTRY_UPDATE_SQL filters broad_codes so these
#  rows are never touched by that UPDATE).
#
#  Date dimension: stats.index_basic_stats.date for the member index
#  (dates where it has a non-NULL close). This ensures the row exists for
#  every trading day the member index has a close, so the UI can compute
#  benchmark_return on-the-fly for any selected date.
#
#  Two-phase implementation using a mapping table:
#    Phase 1 (MEMBER_INDEX_MAP_POPULATE_SQL): TRUNCATE +
#      INSERT...SELECT into analysis.industry_member_index_map — computes
#      the composition-derived weights ONCE (~235 rows, cheap).
#    Phase 2 (MEMBER_INDEX_INSERT_SQL_*): simple JOIN between the mapping
#      table and stats.index_basic_stats dates — no CTE aggregation per
#      date, just a cross-join expansion. Fast even for 400K+ rows.
#
#  Two variants of phase 2:
#    _FULL: no date filter, plain INSERT (TRUNCATE already issued).
#    _INCREMENTAL: ``ibs.date = ANY($1::date[])`` + ON CONFLICT DO UPDATE.

MAP_TABLE = "analysis.industry_member_index_map"

# --- Phase 1: populate the mapping table (composition weights, ONCE) -----
# TRUNCATE + INSERT...SELECT. Uses the same _COMPOSITION_CTES as the
# broad-market INSERT so the composition snapshot is consistent.
# {industry_filter} scopes to one industry (per-industry variant).
_MEMBER_INDEX_MAP_POPULATE_CTE = """
WITH {composition_ctes},
broad_codes AS (
    SELECT DISTINCT code
    FROM stats.sec_index_tags
    WHERE is_broad_market = TRUE
),
-- Distinct (industry_id, member_index_code) pairs, EXCLUDING broad-market
-- codes (already materialized by the broad-market INSERT; excluding them
-- avoids PK conflicts). BROAD_* industries (whose members are all
-- broad-market) thus get NO member-index rows.
member_indices AS (
    SELECT DISTINCT cls.industry_id, h.code AS benchmark_code
    FROM holdings h
    JOIN stats.sec_classification cls
        ON cls.code = h.code AND cls.type = 'index'
    WHERE cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      {industry_filter}
      AND h.code NOT IN (SELECT code FROM broad_codes)
),
-- Total weight per (industry, stock) across ALL same-industry members.
-- Used to compute industry_shared_weight WITHOUT an expensive holdings
-- self-join: for each member M, the other members' overlap with M =
-- SUM over stocks S held by M of (total_weight(S) - M.weight_pct(S)).
industry_stock_weights AS (
    SELECT
        cls.industry_id,
        h.stock_code,
        SUM(h.weight_pct) AS total_weight
    FROM holdings h
    JOIN stats.sec_classification cls
        ON cls.code = h.code AND cls.type = 'index'
    WHERE cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      {industry_filter}
    GROUP BY cls.industry_id, h.stock_code
),
-- industry_shared_weight for each (industry, member_index M):
--   SUM over stocks S held by M of (total_weight(S, industry) -
--   M.weight_pct(S)) = SUM over other same-industry members N of N's
--   weight on stocks shared with M. Self-pair (M, M) excluded by
--   subtracting M's own weight.
member_industry_shared AS (
    SELECT
        cls.industry_id,
        m.code AS benchmark_code,
        SUM(isw.total_weight - m.weight_pct) AS industry_shared_weight
    FROM holdings m
    JOIN stats.sec_classification cls
        ON cls.code = m.code AND cls.type = 'index'
    JOIN industry_stock_weights isw
        ON isw.industry_id = cls.industry_id
       AND isw.stock_code = m.stock_code
    WHERE cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      {industry_filter}
      AND m.code NOT IN (SELECT code FROM broad_codes)
    GROUP BY cls.industry_id, m.code
)
INSERT INTO {map_table}
    (industry_id, benchmark_code,
     industry_shared_weight, benchmark_shared_weight)
SELECT
    mi.industry_id,
    mi.benchmark_code,
    COALESCE(ROUND(mis.industry_shared_weight, 4), 0) AS industry_shared_weight,
    COALESCE(ROUND(bsw.benchmark_shared_weight, 4), 0) AS benchmark_shared_weight
FROM member_indices mi
LEFT JOIN member_industry_shared mis
    ON mis.industry_id = mi.industry_id
   AND mis.benchmark_code = mi.benchmark_code
LEFT JOIN benchmark_shared bsw
    ON bsw.industry_id = mi.industry_id
   AND bsw.benchmark_code = mi.benchmark_code
"""


def _build_member_index_map_sql(
    industry_filter: str = "",
    on_conflict: str = "",
) -> str:
    """Build the member-index map populate INSERT variant."""
    return _MEMBER_INDEX_MAP_POPULATE_CTE.format(
        composition_ctes=_format_composition_ctes(industry_filter),
        map_table=MAP_TABLE,
        industry_filter=industry_filter,
    ) + on_conflict


# ON CONFLICT clause for the map populate INSERT. The map table is
# truncated in force mode, but in incremental mode existing rows from a
# previous run would cause a UniqueViolationError on
# pk_industry_member_index_map (industry_id, benchmark_code). DO UPDATE
# refreshes the composition-derived weights (which only change when
# sec_composition snapshots are refreshed).
_MAP_ON_CONFLICT = """
ON CONFLICT (industry_id, benchmark_code) DO UPDATE SET
    industry_shared_weight  = EXCLUDED.industry_shared_weight,
    benchmark_shared_weight = EXCLUDED.benchmark_shared_weight
"""

# All industries at once.
MEMBER_INDEX_MAP_POPULATE_SQL = _build_member_index_map_sql(
    on_conflict=_MAP_ON_CONFLICT
)

# Per-industry variant — parameter $1 = industry_id.
MEMBER_INDEX_MAP_POPULATE_PER_INDUSTRY_SQL = _build_member_index_map_sql(
    industry_filter="AND cls.industry_id = $1::text",
    on_conflict=_MAP_ON_CONFLICT,
)

# --- Phase 2: expand mapping table to per-date rows (simple JOIN) --------
# No CTE aggregation — just a cross-join between the mapping table and
# stats.index_basic_stats dates. The (code, date) index on index_basic_stats
# makes this fast.
_MEMBER_INDEX_INSERT_BASE = """
INSERT INTO analysis.industry_attributions
    (industry_id, benchmark_code, date,
     industry_shared_weight, benchmark_shared_weight)
SELECT
    m.industry_id,
    m.benchmark_code,
    ibs.date,
    m.industry_shared_weight,
    m.benchmark_shared_weight
FROM {map_table} m
JOIN stats.index_basic_stats ibs
    ON ibs.code = m.benchmark_code AND ibs.close IS NOT NULL
WHERE 1=1
    {date_filter}
    {industry_filter}
"""


def _build_member_index_insert_sql(
    date_filter: str = "",
    industry_filter: str = "",
    on_conflict: str = "",
) -> str:
    """Build a member-index INSERT (phase 2) variant."""
    return _MEMBER_INDEX_INSERT_BASE.format(
        map_table=MAP_TABLE,
        date_filter=date_filter,
        industry_filter=industry_filter,
    ) + on_conflict


# Full recompute (all industries at once) — plain INSERT.
MEMBER_INDEX_INSERT_SQL_FULL = _build_member_index_insert_sql()

# Incremental (all industries at once) — date filter + ON CONFLICT.
MEMBER_INDEX_INSERT_SQL_INCREMENTAL = _build_member_index_insert_sql(
    date_filter="AND ibs.date = ANY($1::date[])",
    on_conflict="""
ON CONFLICT (date, industry_id, benchmark_code) DO UPDATE SET
    industry_shared_weight  = EXCLUDED.industry_shared_weight,
    benchmark_shared_weight = EXCLUDED.benchmark_shared_weight
""",
)

# Per-industry variants — used by the memory-aware loop.
# Parameter $1 = industry_id (full) or $2 = industry_id (incremental, $1 = dates).
MEMBER_INDEX_INSERT_PER_INDUSTRY_FULL = _build_member_index_insert_sql(
    industry_filter="AND m.industry_id = $1::text"
)

MEMBER_INDEX_INSERT_PER_INDUSTRY_INCREMENTAL = _build_member_index_insert_sql(
    date_filter="AND ibs.date = ANY($1::date[])",
    industry_filter="AND m.industry_id = $2::text",
    on_conflict="""
ON CONFLICT (date, industry_id, benchmark_code) DO UPDATE SET
    industry_shared_weight  = EXCLUDED.industry_shared_weight,
    benchmark_shared_weight = EXCLUDED.benchmark_shared_weight
""",
)

# Preview: count distinct industries + member indices that will appear.
PREVIEW_MEMBER_DIMENSIONS_SQL = """
    SELECT
        COUNT(DISTINCT cls.industry_id) AS n_industries,
        COUNT(DISTINCT sc.code) AS n_member_indices
    FROM stats.sec_composition sc
    JOIN stats.sec_classification cls
        ON cls.code = sc.code AND cls.type = 'index'
    WHERE sc.source_type = 'index'
      AND sc.stock_code IS NOT NULL
      AND cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      AND sc.code NOT IN (
          SELECT code FROM stats.sec_index_tags WHERE is_broad_market = TRUE
      )
"""

# Count of non-broad member indices with composition data (for the guard).
COUNT_MEMBER_INDICES_SQL = """
    SELECT COUNT(DISTINCT sc.code) AS n
    FROM stats.sec_composition sc
    JOIN stats.sec_classification cls
        ON cls.code = sc.code AND cls.type = 'index'
    WHERE sc.source_type = 'index'
      AND sc.stock_code IS NOT NULL
      AND cls.industry_id IS NOT NULL
      AND cls.industry_id <> ''
      AND sc.code NOT IN (
          SELECT code FROM stats.sec_index_tags WHERE is_broad_market = TRUE
      )
"""

# Fetch the UNION of industry_ids from BOTH data sources (broad-market
# source sec_alloc_perf_attribution AND member-index source sec_composition)
# so the per-industry loop covers every industry that needs rows.
LIST_INDUSTRY_IDS_SQL = """
    SELECT DISTINCT industry_id FROM (
        -- Industries with broad-market attribution data
        SELECT DISTINCT cls.industry_id
        FROM analysis.sec_alloc_perf_attribution sa
        JOIN (
            SELECT DISTINCT code, industry_id
            FROM stats.sec_classification
            WHERE type = 'index'
              AND industry_id IS NOT NULL
              AND industry_id <> ''
        ) cls ON cls.code = sa.code
        WHERE sa.sec_type = 'index'
          AND sa.code_sec_shared_weight IS NOT NULL
          AND sa.benchmark_code IN (
              SELECT code FROM stats.sec_index_tags WHERE is_broad_market = TRUE
          )
        UNION
        -- Industries with non-broad member indices (composition data)
        SELECT DISTINCT cls.industry_id
        FROM stats.sec_composition sc
        JOIN stats.sec_classification cls
            ON cls.code = sc.code AND cls.type = 'index'
        WHERE sc.source_type = 'index'
          AND sc.stock_code IS NOT NULL
          AND cls.industry_id IS NOT NULL
          AND cls.industry_id <> ''
    ) u
    ORDER BY industry_id
"""


# ---------------------------------------------------------------------------
#  Rolling-column backfill detection
#
#  The non_this_industry_rolling_* columns are populated by Step 5's UPDATE.
#  In incremental mode, that UPDATE carries a date filter (AND ia.date =
#  ANY($1::date[])), so only TARGET dates get rolling prices — historical
#  dates keep whatever they had before (NULL if the columns were added via
#  ALTER TABLE after the initial force run).
#
#  needs_rolling_backfill() lets callers detect this state and trigger a
#  FULL Step 5 backfill that populates ALL dates, not just target dates.
# ---------------------------------------------------------------------------

_ROLLING_BACKFILL_CHECK_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM analysis.industry_attributions
        WHERE benchmark_code IN (
            SELECT code FROM stats.sec_index_tags
            WHERE is_broad_market = TRUE
        )
        AND benchmark_non_this_industry_rolling_255days_price IS NULL
        AND benchmark_shared_weight > 0
    ) AS needs_backfill
"""


async def needs_rolling_backfill(conn) -> bool:
    """Check if broad-market rows have NULL rolling price columns.

    Returns True when existing rows SHOULD have rolling data (broad-market
    benchmark with benchmark_shared_weight > 0) but the
    benchmark_non_this_industry_rolling_255days_price column is NULL —
    typically after a schema change that added the column via ALTER TABLE
    without repopulating it.
    """
    try:
        return bool(await conn.fetchval(_ROLLING_BACKFILL_CHECK_SQL))
    except Exception:
        return False


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

async def run_attributions(
    conn,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    force: bool = False,
    backfill: bool = False,
) -> None:
    """Run the industry-attribution aggregation pipeline.

    Reuses the caller's DB connection (does not open/close its own) so the
    sentiments + correlations + attributions steps form a single
    atomic-ish batch.

    MEMORY-AWARE: INSERTs (broad-market step 4 + member-index step 6) are
    broken into per-industry loops to keep server-side INSERT memory
    bounded. Each iteration INSERTs only one industry's rows. Result
    objects are explicitly del'd and gc.collect() runs every 10 industries.
    The non-this-industry UPDATE (step 5) runs all-at-once because its CTE
    chain (window functions over full stock/benchmark history) is too
    expensive to recompute per-industry.

    Pipeline
      1. Guard: if BOTH sec_alloc_perf_attribution is empty AND there are
         no member indices with composition data, exit gracefully.
      2. Preview: report distinct industries x benchmarks + member indices.
      3. Force mode: TRUNCATE analysis.industry_attributions +
         industry_member_index_map. Incremental mode: no truncate.
      4. Broad-market INSERT (per-industry, memory-aware): inserts each
         industry's broad-market benchmark rows one industry at a time.
      5. Non-this-industry UPDATE (all-at-once): computes
         benchmark_non_this_industry_* columns for broad-market benchmarks.
      6. Member-index INSERT (per-industry, memory-aware): populates the
         mapping table + expands to per-date rows, one industry at a time.
      7. Upsert analysis.analysis_identity (name='industry_attributions'
         + name='industry_member_index_map').
      8. Sanity summary by benchmark_code (broad-market + member-index).

    Args:
      target_dates: when non-empty (and force=False), only rows whose date
        is in this set are upserted (incremental mode).
      force: when True, truncate the table first and recompute all rows.
      backfill: when True, skip Steps 1-4 and 6 (INSERT/truncate) and
        only run Step 5 (FULL non-this-industry UPDATE) to populate
        rolling price columns for ALL existing rows. Used when the
        rolling columns were added via ALTER TABLE after the initial
        force run and the incremental date filter would miss historical
        dates. Also runs Step 7 (identity) + Step 8 (summary).
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  INDUSTRY ATTRIBUTIONS (internal step of industry_sentiments)",
          flush=True)
    print("=" * 78, flush=True)

    incremental = (not force
                   and target_dates is not None
                   and len(target_dates) > 0)
    if backfill:
        print("    mode: BACKFILL (rolling price columns only)",
              flush=True)
    elif force:
        print("    mode: FORCE (full recompute)", flush=True)
    elif incremental:
        print(f"    mode: incremental ({len(target_dates)} target dates)",
              flush=True)

    # ---- Backfill mode: skip straight to Step 5 (FULL UPDATE) ----------
    # Existing rows already have industry_shared_weight + benchmark_shared_weight
    # (from a prior force/incremental run). We only need to (re)compute the
    # non_this_industry_* columns — especially the rolling price columns
    # that may be NULL after an ALTER TABLE ADD COLUMN.
    if backfill:
        n_src = await conn.fetchval(COUNT_SOURCE_SQL)
        if not n_src:
            print("\n[a1/6] sec_alloc_perf_attribution empty — "
                  "nothing to backfill.", flush=True)
            return
        await conn.execute(SET_WORK_MEM_SQL)
        t_non_ind = time.time()
        print("\n[a5/6] Non-this-industry UPDATE (FULL backfill, "
              "broad-market only)...", flush=True)
        status_ni = await conn.execute(NON_THIS_INDUSTRY_SQL_FULL)
        n_updated = _parse_update_count(status_ni)
        print(f"      -> {status_ni} | {n_updated:,} rows updated "
              f"({time.time() - t_non_ind:.1f}s)", flush=True)
        del status_ni
        gc.collect()

        # Step 7: upsert analysis_identity
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name=ANALYSIS_NAME,
            description=ANALYSIS_DESCRIPTION,
        )

        print(f"\n  attributions wall time: {time.time() - t0:.1f}s",
              flush=True)
        return

    # ---- Step 1: guard — check upstream + member-index availability --
    # The broad-market INSERT needs sec_alloc_perf_attribution; the
    # member-index INSERT only needs sec_composition. Only exit if BOTH
    # are empty (nothing to materialize at all).
    n_src = await conn.fetchval(COUNT_SOURCE_SQL)
    n_members = await conn.fetchval(COUNT_MEMBER_INDICES_SQL)
    if not n_src and not n_members:
        print("\n[a1/6] sec_alloc_perf_attribution has no index rows AND "
              "no member indices with composition data — nothing to "
              "materialize. Skipping attributions step.", flush=True)
        return
    print(f"\n[a1/6] Source analysis.sec_alloc_perf_attribution: "
          f"{n_src:,} index rows | {n_members} non-broad member indices "
          f"with composition data.", flush=True)

    # ---- Step 2: preview dimensions ----------------------------------
    print("\n[a2/6] Previewing output dimensions...", flush=True)
    dims = await conn.fetchrow(PREVIEW_DIMENSIONS_SQL)
    n_industries = dims["n_industries"] if dims else 0
    n_benchmarks = dims["n_benchmarks"] if dims else 0
    print(f"      broad-market: {n_industries} industries x {n_benchmarks} "
          f"benchmarks (max {n_industries * n_benchmarks:,} pairs, "
          f"materialized per date where member indices have data)",
          flush=True)
    mdims = await conn.fetchrow(PREVIEW_MEMBER_DIMENSIONS_SQL)
    n_md_ind = mdims["n_industries"] if mdims else 0
    n_md_mem = mdims["n_member_indices"] if mdims else 0
    print(f"      member-index: {n_md_ind} industries x {n_md_mem} "
          f"non-broad member indices", flush=True)

    # ---- Step 3: truncate (full recompute only) ----------------------
    # Full recompute (force OR no target_dates) requires truncate first.
    # Incremental mode skips truncate and relies on ON CONFLICT DO UPDATE.
    if not incremental:
        print(f"\n[a3/6] Truncating {TABLE} + {MAP_TABLE} (full recompute)...",
              flush=True)
        await truncate_table_async(conn, TABLE)
        await truncate_table_async(conn, MAP_TABLE)
    else:
        print(f"\n[a3/6] Incremental mode — no truncate "
              f"(ON CONFLICT DO UPDATE handles dedup).", flush=True)

    # ---- Step 4: broad-market INSERT (per-industry, memory-aware) ----
    # Process ONE industry at a time to keep server-side INSERT memory
    # bounded. Each iteration INSERTs only this industry's rows (~30K
    # per industry vs 2.6M all-at-once). Result objects are explicitly
    # del'd and gc.collect() runs every 10 industries.
    industry_rows = await conn.fetch(LIST_INDUSTRY_IDS_SQL)
    n_industries_total = len(industry_rows)
    await conn.execute(SET_WORK_MEM_SQL)
    sorted_dates = sorted(target_dates) if incremental else []
    t_loop = time.time()
    n_total_broad = 0
    n_total_member = 0
    n_total_map = 0

    print(f"\n[a4/6] Broad-market INSERT (per-industry, {n_industries_total} "
          f"industries, memory-aware)...", flush=True)
    for i, irow in enumerate(industry_rows, 1):
        industry_id = irow["industry_id"]
        t_ind = time.time()
        if n_src:
            if incremental:
                status = await conn.execute(
                    BROAD_MARKET_INSERT_PER_INDUSTRY_INCREMENTAL,
                    sorted_dates, industry_id,
                )
            else:
                status = await conn.execute(
                    BROAD_MARKET_INSERT_PER_INDUSTRY_FULL, industry_id
                )
            n_broad = _parse_insert_count(status)
            n_total_broad += n_broad
            del status
        else:
            n_broad = 0
        print(f"  [{i:>3}/{n_industries_total}] {industry_id:20s}: "
              f"broad={n_broad:>7,} ({time.time() - t_ind:.1f}s)", flush=True)
        del industry_id, n_broad, t_ind
        if i % 10 == 0:
            gc.collect()
    gc.collect()
    print(f"  broad-market total: {n_total_broad:,} rows "
          f"in {time.time() - t_loop:.1f}s", flush=True)

    # ---- Step 5: non-this-industry UPDATE (all-at-once) --------------
    # This is an UPDATE (not INSERT), so memory is not the concern. The
    # CTE chain (holdings, stock_daily, bench_daily with window functions)
    # is too expensive to recompute per-industry (84x overhead), so it
    # runs once for all industries. The {date_filter} limits which rows
    # are touched in incremental mode.
    #
    # AUTO-BACKFILL: in incremental mode, if existing rows have NULL
    # rolling price columns (e.g., after ALTER TABLE ADD COLUMN), the
    # date-filtered UPDATE would only fix target dates — historical
    # dates would stay NULL. Detect this and fall back to FULL mode so
    # ALL dates get rolling prices in a single pass.
    t_non_ind = time.time()
    if not n_src:
        print("\n[a5/6] SKIPPED (no broad-market rows to update).",
              flush=True)
    elif incremental:
        rolling_backfill = await conn.fetchval(_ROLLING_BACKFILL_CHECK_SQL)
        if rolling_backfill:
            print(f"\n[a5/6] Non-this-industry UPDATE (FULL backfill — "
                  f"existing rows have NULL rolling prices, "
                  f"broad-market only)...", flush=True)
            status_ni = await conn.execute(NON_THIS_INDUSTRY_SQL_FULL)
        else:
            print(f"\n[a5/6] Non-this-industry UPDATE (incremental, "
                  f"{len(sorted_dates)} target dates, "
                  f"broad-market only)...", flush=True)
            status_ni = await conn.execute(
                NON_THIS_INDUSTRY_SQL_INCREMENTAL, sorted_dates
            )
        n_updated = _parse_update_count(status_ni)
        print(f"      -> {status_ni} | {n_updated:,} rows updated "
              f"({time.time() - t_non_ind:.1f}s)", flush=True)
        del status_ni
    else:
        print("\n[a5/6] Non-this-industry UPDATE (full, broad-market "
              "only)...", flush=True)
        status_ni = await conn.execute(NON_THIS_INDUSTRY_SQL_FULL)
        n_updated = _parse_update_count(status_ni)
        print(f"      -> {status_ni} | {n_updated:,} rows updated "
              f"({time.time() - t_non_ind:.1f}s)", flush=True)
        del status_ni
    gc.collect()

    # ---- Step 6: member-index (per-industry, memory-aware) -----------
    # Per-industry INSERT to keep memory bounded. Phase 1 populates the
    # mapping table for this industry; phase 2 expands to per-date rows.
    t_member = time.time()
    print(f"\n[a6/6] Member-index INSERT (per-industry, "
          f"{n_industries_total} industries, memory-aware)...", flush=True)
    for i, irow in enumerate(industry_rows, 1):
        industry_id = irow["industry_id"]
        t_ind = time.time()

        # Phase 1: populate mapping table for this industry.
        status_map = await conn.execute(
            MEMBER_INDEX_MAP_POPULATE_PER_INDUSTRY_SQL, industry_id
        )
        n_map = _parse_insert_count(status_map)
        n_total_map += n_map
        del status_map

        # Phase 2: expand to per-date rows for this industry.
        if incremental:
            status_mi = await conn.execute(
                MEMBER_INDEX_INSERT_PER_INDUSTRY_INCREMENTAL,
                sorted_dates, industry_id,
            )
        else:
            status_mi = await conn.execute(
                MEMBER_INDEX_INSERT_PER_INDUSTRY_FULL, industry_id
            )
        n_mi = _parse_insert_count(status_mi)
        n_total_member += n_mi
        del status_mi

        print(f"  [{i:>3}/{n_industries_total}] {industry_id:20s}: "
              f"map={n_map:>3} member={n_mi:>7,} "
              f"({time.time() - t_ind:.1f}s)", flush=True)
        del industry_id, n_map, n_mi, t_ind
        if i % 10 == 0:
            gc.collect()
    gc.collect()
    del industry_rows
    print(f"  member-index total: {n_total_member:,} rows (map={n_total_map}) "
          f"in {time.time() - t_member:.1f}s", flush=True)

    # Upsert analysis_identity for the mapping table.
    await upsert_analysis_identity(
        conn,
        name="industry_member_index_map",
        detail_name="industry_member_index_map",
        description=(
            "Pre-computed mapping of each industry to its NON-BROAD "
            "member indices, with composition-derived shared weights "
            "frozen at the latest sec_composition snapshot. Used to "
            "fast-track analysis.industry_attributions population."
        ),
    )

    # ---- Step 7: upsert analysis_identity ----------------------------
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME,
        detail_name=ANALYSIS_NAME,
        description=ANALYSIS_DESCRIPTION,
    )

    # ---- Step 8: sanity summary --------------------------------------
    summary = await conn.fetch("""
        SELECT ia.benchmark_code,
               BOOL_OR(sit.is_broad_market) AS is_broad,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT ia.industry_id) AS n_industries,
               MIN(ia.date) AS first_date,
               MAX(ia.date) AS last_date,
               ROUND(AVG(ia.industry_shared_weight), 4) AS avg_isw,
               ROUND(AVG(ia.benchmark_shared_weight), 4) AS avg_bsw,
               COUNT(*) FILTER (WHERE ia.industry_shared_weight = 0
                                 AND ia.benchmark_shared_weight = 0)
                   AS n_zero_overlap
        FROM analysis.industry_attributions ia
        LEFT JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
        GROUP BY ia.benchmark_code
        ORDER BY is_broad DESC, n_rows DESC
        LIMIT 20
    """)
    print("\n      Summary by benchmark_code (top 20, broad-market first):",
          flush=True)
    for r in summary:
        tag = "BROAD" if r["is_broad"] else "MEMBER"
        print(f"        {r['benchmark_code']:8s} [{tag}]: "
              f"{r['n_rows']:>9,} rows . {r['n_industries']:>3} ind . "
              f"{r['first_date']} -> {r['last_date']} . "
              f"avg_isw={r['avg_isw']} avg_bsw={r['avg_bsw']} . "
              f"zero_overlap={r['n_zero_overlap']:,}", flush=True)

    print(f"\n  attributions wall time: {time.time() - t0:.1f}s", flush=True)


def _parse_insert_count(status: str) -> int:
    """Parse the row count from an asyncpg INSERT status string.

    asyncpg ``Connection.execute`` returns a status like
    ``"INSERT 0 18128883"``. The third token is the inserted row count.
    Returns 0 if the status can't be parsed.
    """
    if not status:
        return 0
    parts = status.split()
    if len(parts) >= 3 and parts[0] == "INSERT":
        try:
            return int(parts[2])
        except ValueError:
            return 0
    return 0


def _parse_update_count(status: str) -> int:
    """Parse the row count from an asyncpg UPDATE status string.

    asyncpg ``Connection.execute`` returns a status like
    ``"UPDATE 12345"``. The second token is the updated row count.
    Returns 0 if the status can't be parsed.
    """
    if not status:
        return 0
    parts = status.split()
    if len(parts) >= 2 and parts[0] == "UPDATE":
        try:
            return int(parts[1])
        except ValueError:
            return 0
    return 0
