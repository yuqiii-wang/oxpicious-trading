"""Internal attributions step for analyze.industry_sentiments.

Industry-level composition overlap between each industry (as a group of
member indices) and each benchmark index.

Populates analysis.industry_attributions with one row per
(date, industry_id, benchmark_code) where:

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
                            hold the same stock. In percent (0-100), bounded
                            [0, 100] — weight_pct is stored as a percent,
                            not a fraction. Recomputed from sec_composition
                            (NOT summed from sec_alloc_perf_attribution)
                            because a naive SUM of
                            benchmark_sec_shared_weight across members
                            would double-count stocks held by multiple
                            members.

HYBRID AGGREGATION RATIONALE
  industry_shared_weight is a clean SUM (each member contributes a DISTINCT
  portfolio's weight, so summing is valid). benchmark_shared_weight is NOT
  a clean SUM — the benchmark's weight on a stock is the SAME regardless of
  which member we pair it with, so a stock held by N members would have its
  benchmark weight counted N times. Recomputing from the union of industry
  member stocks avoids this.

IMPLEMENTATION
  The aggregation is pure SQL, so the whole transform runs server-side as a
  single INSERT ... SELECT statement.

  Force mode: TRUNCATE then INSERT...SELECT (full recompute).
  Incremental mode: INSERT...SELECT with a date filter
  (``sa.date = ANY($1::date[])``) + ON CONFLICT DO UPDATE (no truncate).

DEPENDENCY
  This step reads analysis.sec_alloc_perf_attribution, which is populated
  by analyze.sec_alloc_perf_attribution. If that table is empty (the
  upstream analysis has not been run), this step produces no rows and exits
  gracefully.

This module is an INTERNAL step of analyze.industry_sentiments — it is
invoked from __main__.py after the sentiments + correlations steps,
reusing the same DB connection. It is NOT a standalone runnable.
"""
from __future__ import annotations

import datetime
import time
from typing import Optional, Set

from utils.build_commons import (
    truncate_table_async,
)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_attributions"
ANALYSIS_NAME = "industry_attributions"
ANALYSIS_DESCRIPTION = (
    "Composition overlap between each industry (group of member indices) "
    "and each benchmark index. One row per (date, industry_id, "
    "benchmark_code). HYBRID aggregation: industry_shared_weight = "
    "SUM(code_sec_shared_weight) across member indices from "
    "analysis.sec_alloc_perf_attribution (own-weight on shared stocks in "
    "percent, can exceed 100, self-pairs excluded); "
    "benchmark_shared_weight = "
    "benchmark weight on the UNION of industry member stocks from "
    "stats.sec_composition (latest snapshot, bounded [0, 100] (percent), "
    "no double-counting, recomputed from compositions to "
    "avoid double-counting stocks held by multiple members). Both use "
    "LATEST snapshot for all dates (weight_pct is stored as a percent, "
    "not a fraction). Built by analyze.industry_sentiments.attributions "
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
_CTE_PREFIX = f"""
WITH {_COMPOSITION_CTES},
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
        FROM stats.sec_classification
        WHERE type = 'index'
          AND industry_id IS NOT NULL
          AND industry_id <> ''
    ) cls ON cls.code = sa.code
    WHERE sa.sec_type = 'index'
      AND sa.benchmark_code IN (SELECT code FROM broad_codes)
    {{date_filter}}
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

# Full recompute: no date filter, plain INSERT (TRUNCATE issued separately).
INSERT_SELECT_SQL_FULL = _CTE_PREFIX.format(date_filter="")

# Incremental: date filter on industry_shared + ON CONFLICT DO UPDATE so
# only target-date rows are upserted without truncating.
INSERT_SELECT_SQL_INCREMENTAL = _CTE_PREFIX.format(
    date_filter="AND sa.date = ANY($1::date[])"
) + """
ON CONFLICT (date, industry_id, benchmark_code) DO UPDATE SET
    industry_shared_weight  = EXCLUDED.industry_shared_weight,
    benchmark_shared_weight = EXCLUDED.benchmark_shared_weight
"""

# Bump work_mem for the big hash aggregate so the GROUP BY on ~44M source
# rows doesn't spill to disk excessively. Session-scoped (restored on
# reconnect; this step owns the connection for its duration).
SET_WORK_MEM_SQL = "SET work_mem = '512MB'"

# ---------------------------------------------------------------------------
#  Non-this-industry price / rolling_price / trading_amt computation.
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
#    rolling_price  = 100 × exp(sum(ln(1 + non_industry_return))) from start
#    trading_amt    = bench.trading_amount - SUM(shared_stock.trading_amount)
#
#  The CTE chain reuses _COMPOSITION_CTES (latest/holdings/industry_stocks/
#  benchmark_shared) so the composition snapshot is computed once per
#  connection, then layers on stock + benchmark price data.
#
#  {date_filter} placeholder: in incremental mode, adds
#  ``AND ia.date = ANY($1::date[])`` to the UPDATE WHERE so only target-date
#  rows are touched (the CTEs still compute full history for rolling_price).
_NON_THIS_INDUSTRY_UPDATE_SQL = f"""
WITH {_COMPOSITION_CTES},
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
stock_daily AS (
    SELECT
        usc.stock_code,
        sbs.date,
        sbs.close,
        sbs.trading_amount,
        LAG(sbs.close) OVER w AS prev_close
    FROM unique_stock_codes usc
    JOIN stats.stock_basic_stats sbs ON sbs.code = usc.stock_code
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
-- Final computed values (window function for rolling cumprod)
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
        -- rolling_price: 100 * exp(sum(ln(1 + return))) from start.
        -- Returns outside [-0.5, 0.5] are treated as 0 (no change) to
        -- prevent artifacts from compounding. NULL returns also treated
        -- as 0 so cumprod carries forward.
        100.0 * exp(
            SUM(CASE
                WHEN nir.non_industry_return IS NOT NULL
                     AND nir.non_industry_return > -0.5
                     AND nir.non_industry_return <= 0.5
                THEN ln(1.0 + nir.non_industry_return)
                ELSE 0
            END) OVER (
                PARTITION BY nir.industry_id, nir.benchmark_code
                ORDER BY nir.date
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            )
        ) AS non_this_industry_rolling_price,
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
    benchmark_non_this_industry_rolling_price = c.non_this_industry_rolling_price,
    benchmark_non_this_industry_trading_amt = c.non_this_industry_trading_amt
FROM computed c
WHERE ia.industry_id = c.industry_id
  AND ia.benchmark_code = c.benchmark_code
  AND ia.date = c.date
  {{date_filter}}
"""

# Full recompute: no date filter.
NON_THIS_INDUSTRY_SQL_FULL = _NON_THIS_INDUSTRY_UPDATE_SQL.format(date_filter="")

# Incremental: date filter on the UPDATE so only target-date rows are touched.
NON_THIS_INDUSTRY_SQL_INCREMENTAL = _NON_THIS_INDUSTRY_UPDATE_SQL.format(
    date_filter="AND ia.date = ANY($1::date[])"
)


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

async def run_attributions(
    conn,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    force: bool = False,
) -> None:
    """Run the industry-attribution aggregation pipeline.

    Reuses the caller's DB connection (does not open/close its own) so the
    sentiments + correlations + attributions steps form a single
    atomic-ish batch.

    Pipeline
      1. Guard: if analysis.sec_alloc_perf_attribution has no index rows,
         exit gracefully (upstream analysis not run yet).
      2. Preview: report distinct industries x benchmarks that will appear.
      3. Force mode: TRUNCATE analysis.industry_attributions.
         Incremental mode: no truncate (ON CONFLICT DO UPDATE handles
         deduplication).
      4. INSERT ... SELECT (server-side): the full hybrid aggregation.
         Incremental mode adds a date filter + ON CONFLICT DO UPDATE.
      5. Upsert analysis.analysis_identity (name='industry_attributions').
      6. Sanity summary by benchmark_code.

    Args:
      target_dates: when non-empty (and force=False), only rows whose date
        is in this set are upserted (incremental mode).
      force: when True, truncate the table first and recompute all rows.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  INDUSTRY ATTRIBUTIONS (internal step of industry_sentiments)",
          flush=True)
    print("=" * 78, flush=True)

    incremental = (not force
                   and target_dates is not None
                   and len(target_dates) > 0)
    if force:
        print("    mode: FORCE (full recompute)", flush=True)
    elif incremental:
        print(f"    mode: incremental ({len(target_dates)} target dates)",
              flush=True)

    # ---- Step 1: guard — upstream table must have index rows ----------
    n_src = await conn.fetchval(COUNT_SOURCE_SQL)
    if not n_src:
        print("\n[a1/5] analysis.sec_alloc_perf_attribution has no index "
              "rows — upstream analysis not run yet. Skipping attributions "
              "step.", flush=True)
        return
    print(f"\n[a1/5] Source analysis.sec_alloc_perf_attribution: "
          f"{n_src:,} index rows available.", flush=True)

    # ---- Step 2: preview dimensions ----------------------------------
    print("\n[a2/5] Previewing output dimensions (distinct industries x "
          "benchmarks with non-NULL shared weight)...", flush=True)
    dims = await conn.fetchrow(PREVIEW_DIMENSIONS_SQL)
    n_industries = dims["n_industries"] if dims else 0
    n_benchmarks = dims["n_benchmarks"] if dims else 0
    print(f"      -> {n_industries} industries x {n_benchmarks} benchmarks "
          f"(max {n_industries * n_benchmarks:,} pairs, materialized per "
          f"date where member indices have data)", flush=True)

    # ---- Step 3: truncate (full recompute only) ----------------------
    # Full recompute (force OR no target_dates) requires truncate first.
    # Incremental mode skips truncate and relies on ON CONFLICT DO UPDATE.
    if not incremental:
        print(f"\n[a3/5] Truncating {TABLE} (full recompute)...", flush=True)
        await truncate_table_async(conn, TABLE)
    else:
        print(f"\n[a3/5] Incremental mode — no truncate "
              f"(ON CONFLICT DO UPDATE handles dedup).", flush=True)

    # ---- Step 4: INSERT ... SELECT (server-side) ---------------------
    await conn.execute(SET_WORK_MEM_SQL)
    t_insert = time.time()
    if incremental:
        sorted_dates = sorted(target_dates)
        print(f"\n[a4/5] Running server-side INSERT...SELECT (incremental, "
              f"{len(sorted_dates)} target dates, ON CONFLICT DO UPDATE)...",
              flush=True)
        status = await conn.execute(
            INSERT_SELECT_SQL_INCREMENTAL, sorted_dates
        )
    else:
        print("\n[a4/5] Running server-side INSERT...SELECT (full, hybrid "
              "aggregation: SUM from sec_alloc_perf_attribution + benchmark "
              "weight on union from sec_composition)...", flush=True)
        status = await conn.execute(INSERT_SELECT_SQL_FULL)

    # asyncpg execute() returns a status string like "INSERT 0 18128883".
    # With ON CONFLICT the count includes both inserted and updated rows.
    n_inserted = _parse_insert_count(status)
    print(f"      -> {status} | {n_inserted:,} rows affected "
          f"({time.time() - t_insert:.1f}s)", flush=True)

    # ---- Step 5: non-this-industry price / rolling_price / trading_amt
    # UPDATE the three new columns for broad-market benchmarks only (the
    # decomposition formula requires a broad-market benchmark). All other
    # benchmarks keep NULL in these columns.
    # The CTE chain computes full history (needed for the rolling cumprod
    # window function) even in incremental mode; the {date_filter} on the
    # UPDATE WHERE limits which rows are actually touched.
    t_non_ind = time.time()
    if incremental:
        sorted_dates = sorted(target_dates)
        print(f"\n[a5/5] Running server-side UPDATE for non-this-industry "
              f"metrics (incremental, {len(sorted_dates)} target dates, "
              f"broad-market benchmarks only)...", flush=True)
        status_ni = await conn.execute(
            NON_THIS_INDUSTRY_SQL_INCREMENTAL, sorted_dates
        )
    else:
        print("\n[a5/5] Running server-side UPDATE for non-this-industry "
              "metrics (full recompute, broad-market benchmarks only)...",
              flush=True)
        status_ni = await conn.execute(NON_THIS_INDUSTRY_SQL_FULL)
    n_updated = _parse_update_count(status_ni)
    print(f"      -> {status_ni} | {n_updated:,} rows updated "
          f"({time.time() - t_non_ind:.1f}s)", flush=True)

    # ---- Step 6: upsert analysis_identity ----------------------------
    await conn.execute("""
        INSERT INTO analysis.analysis_identity
            (name, detail_name, summary_name, last_run_datetime, description)
        VALUES ($1, $2, NULL, NOW(), $3)
        ON CONFLICT (name) DO UPDATE SET
            detail_name       = EXCLUDED.detail_name,
            summary_name      = EXCLUDED.summary_name,
            last_run_datetime = NOW(),
            description       = EXCLUDED.description
    """, ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
    print(f"      -> upserted analysis_identity "
          f"(name='{ANALYSIS_NAME}')", flush=True)

    # ---- Step 7: sanity summary --------------------------------------
    summary = await conn.fetch("""
        SELECT benchmark_code,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT industry_id) AS n_industries,
               MIN(date) AS first_date,
               MAX(date) AS last_date,
               ROUND(AVG(industry_shared_weight), 4) AS avg_isw,
               ROUND(AVG(benchmark_shared_weight), 4) AS avg_bsw,
               COUNT(*) FILTER (WHERE industry_shared_weight = 0
                                  AND benchmark_shared_weight = 0)
                   AS n_zero_overlap
        FROM analysis.industry_attributions
        GROUP BY benchmark_code
        ORDER BY n_rows DESC
        LIMIT 12
    """)
    print("\n      Summary by benchmark_code (top 12):", flush=True)
    for r in summary:
        print(f"        {r['benchmark_code']:8s}: "
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
