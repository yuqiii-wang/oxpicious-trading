"""Internal hypes_and_drains step for analyze.industry_sentiments.

Pre-computes the top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by their
hype (industry_return - benchmark_return) relative to a BROAD-MARKET benchmark
over a trailing window, for every (date, benchmark_code, period, weighting).

Populates analysis.industry_hypes_and_drains with 10 rows per
(date, benchmark_code, period, weighting): rank 1..5 HYPE + rank 1..5 DRAIN.

BENCHMARKS
  Uses ALL broad-market benchmarks that appear in analysis.industry_attributions
  (i.e. all benchmark_codes with is_broad_market=TRUE in stats.sec_index_tags).
  This is the SAME set offered by the Benchmark Attribution dropdown — the UI
  reuses the same Autocomplete.

METHODOLOGY (hype = industry_return - benchmark_return)
  For each (date, industry, benchmark, period N):
    non_industry_return_Nd = benchmark.benchmark_non_this_industry_rolling_{N}days_price
                             / 100 - 1   (cumulative non-industry return factor
                             over the trailing N trading days)
    benchmark_return_Nd    = benchmark.close[t] / benchmark.close[t-N] - 1
    swf                    = benchmark_shared_weight / 100.0
    industry_return_Nd     = (benchmark_return_Nd - (1 - swf) * non_industry_return_Nd) / swf
    hype                   = industry_return_Nd - benchmark_return_Nd

  Positive hype = HYPE (industry's shared stocks outperformed the benchmark);
  negative = DRAIN (underperformed).

  Industries with NULL non_industry_return (no overlap with the benchmark, or
  insufficient history) or swf = 0 are excluded from ranking.

WEIGHTING VARIANTS
  Two weighting variants are materialized, one per attribution_type:
    'equal'       (attribution_type='equal'):       metric_value = hype
    'amt'         (attribution_type='trading_amt'): metric_value = hype * shared_trading_amt

  The pipeline calls the INSERT SQL twice per (benchmark, period) — once for
  each attribution_type. The weighting column in industry_hypes_and_drains
  maps directly: 'equal' -> 'equal', 'trading_amt' -> 'amt'.

PERIODS: {5, 20, 60, 120, 255, 500}. 120d is the UI default. The 120d
column on industry_attributions is added by 08_industry_hypes_and_drains.sql
and populated by the attributions step (ROLLING_WINDOWS includes 120).

This module is an INTERNAL step of analyze.industry_sentiments — it is
invoked from __main__.py after the attributions step, reusing the same DB
connection. It is NOT a standalone runnable.
"""
from __future__ import annotations

import gc
import time

from _common.build_commons import (
    truncate_table_async,
)
from analyze._common import upsert_analysis_identity


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_hypes_and_drains"
SEASONAL_TABLE = "analysis.industry_hypes_seasonal"
ANALYSIS_NAME = "industry_hypes_and_drains"
ANALYSIS_DESCRIPTION = (
    "Pre-computed top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by "
    "hype (industry_return - benchmark_return) relative to a BROAD-MARKET "
    "benchmark over a trailing window. One row per (date, benchmark_code, "
    "period_days, weighting, rank_side, rank). Two weighting variants: "
    "'equal' (metric_value = hype, attribution_type='equal') and 'amt' "
    "(metric_value = hype * shared_trading_amt, attribution_type="
    "'trading_amt'). hype = industry_return_Nd - benchmark_return_Nd "
    "where industry_return_Nd = (bench_ret - (1-swf)*non_ind_ret) / swf "
    "and swf = benchmark_shared_weight / 100. period_days in "
    "{5,20,60,120,255,500} (120 default). Built by "
    "analyze.industry_sentiments.hypes_and_drains (internal step, "
    "truncate-then-recompute). Depends on analysis.industry_attributions "
    "(incl. 120d column) being populated first."
)

# Trailing windows (trading days). Must match the rolling_{N}days_price
# columns materialized in analysis.industry_attributions (see
# attributions.ROLLING_WINDOWS). 120d is the UI default.
PERIODS: tuple[int, ...] = (5, 20, 60, 120, 255, 500)


# ---------------------------------------------------------------------------
#  SQL
# ---------------------------------------------------------------------------

# Fetch all broad-market benchmark codes that have industry_attributions data.
# Filter on attribution_type='trading_amt' to avoid duplicate benchmarks (each
# benchmark now appears twice — once per attribution_type).
BROAD_MARKET_BENCHMARKS_SQL = """
    SELECT DISTINCT ia.benchmark_code
    FROM analysis.industry_attributions ia
    JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
    WHERE sit.is_broad_market = TRUE
      AND ia.attribution_type = 'trading_amt'
    ORDER BY ia.benchmark_code
"""

# Guard: bail out early if the upstream table is empty / missing OR the 255d
# column (required for the longest period) has not been populated.
# Filter on attribution_type='trading_amt' — the non_this_industry UPDATE only
# populates trading_amt rows; equal rows inherit via the equal-variant INSERT.
COUNT_SOURCE_SQL = """
    SELECT COUNT(*) AS n
    FROM analysis.industry_attributions ia
    JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
    WHERE sit.is_broad_market = TRUE
      AND ia.attribution_type = 'trading_amt'
      AND ia.benchmark_non_this_industry_rolling_255days_price IS NOT NULL
"""

# Check that the 120d column exists and has data.
COUNT_120D_SQL = """
    SELECT COUNT(*) AS n
    FROM analysis.industry_attributions ia
    JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
    WHERE sit.is_broad_market = TRUE
      AND ia.attribution_type = 'trading_amt'
      AND ia.benchmark_non_this_industry_rolling_120days_price IS NOT NULL
"""


def _rolling_col(period: int) -> str:
    """Return the industry_attributions column name for the given period."""
    return f"benchmark_non_this_industry_rolling_{period}days_price"


# Per-(benchmark_code, period, attribution_type) INSERT...SELECT.
#
# Builds the full pipeline server-side for ONE (benchmark, period, variant):
#   bench_daily       — per (benchmark, date): close + LAG(close, N) via a
#                       PARTITION BY code window.
#   bench_returns     — benchmark_return_Nd = close/close_n_ago - 1.
#   industry_non_ind  — per (date, industry): the pre-materialized
#                       non_this_industry_rolling_{N}days_price / 100 - 1,
#                       filtered to non-NULL (drops industries with no overlap
#                       with this benchmark for this period).
#   per_industry      — JOIN bench_returns x industry_non_ind on date.
#                       Computes hype = industry_return_nd - benchmark_return_nd
#                       where industry_return_nd is derived from the return
#                       decomposition: swf = benchmark_shared_weight / 100;
#                       industry_return_nd = (bench_ret - (1-swf)*non_ind_ret) / swf.
#   per_industry_metric — computes metric_value based on attribution_type:
#                       'equal' -> hype; 'trading_amt' -> hype * shared_trading_amt.
#   ranked            — ROW_NUMBER() OVER (PARTITION BY date ORDER BY
#                       metric_value DESC/ASC) for HYPE/DRAIN, filter <= 5.
#
# Parameters:
#   $1 = period N (int)
#   $2 = benchmark_code (text)
#   $3 = attribution_type (text) — 'equal' or 'trading_amt'
# The {rolling_col} and {period} placeholders are format-substituted.
# benchmark_code is validated from the DB, rolling_col is a frozen column
# name from _rolling_col(period).
#
# The weighting column in industry_hypes_and_drains is derived from
# attribution_type: 'equal' -> 'equal', 'trading_amt' -> 'amt'.
_INSERT_SQL_TEMPLATE = """
WITH bench_daily AS (
    SELECT
        ib.date,
        ib.close,
        ib.trading_amount,
        LAG(ib.close, $1::int) OVER w AS close_n_ago
    FROM stats.index_basic_stats ib
    WHERE ib.code = $2::text
      AND ib.close IS NOT NULL
    WINDOW w AS (ORDER BY ib.date)
),
bench_returns AS (
    SELECT
        date,
        trading_amount,
        CASE
            WHEN close_n_ago IS NOT NULL AND close_n_ago != 0
            THEN close / close_n_ago - 1.0
            ELSE NULL
        END AS benchmark_return_nd
    FROM bench_daily
),
industry_non_ind AS (
    SELECT
        ia.date,
        ia.industry_id,
        ia.benchmark_shared_weight,
        ia.{rolling_col} / 100.0 - 1.0 AS non_industry_return_nd,
        ia.benchmark_non_this_industry_trading_amt
    FROM analysis.industry_attributions ia
    WHERE ia.benchmark_code = $2::text
      AND ia.attribution_type = $3::text
      AND ia.{rolling_col} IS NOT NULL
),
per_industry AS (
    SELECT
        br.date,
        pc.industry_id,
        pc.benchmark_shared_weight,
        pc.non_industry_return_nd,
        br.benchmark_return_nd,
        -- hype = industry_return_nd - benchmark_return_nd
        -- industry_return_nd = (bench_ret - (1-swf)*non_ind_ret) / swf
        -- swf = benchmark_shared_weight / 100.0
        -- Guard: swf = 0 -> NULL (division by zero; industry has no overlap
        -- with the benchmark, so hype is undefined).
        CASE
            WHEN pc.benchmark_shared_weight IS NULL
                 OR pc.benchmark_shared_weight = 0 THEN NULL
            ELSE (
                (br.benchmark_return_nd
                 - (1.0 - pc.benchmark_shared_weight / 100.0)
                   * pc.non_industry_return_nd)
                / (pc.benchmark_shared_weight / 100.0)
            ) - br.benchmark_return_nd
        END AS hype,
        -- shared_trading_amt = benchmark total trading - non-industry trading.
        -- NULL when either component is missing or result is non-positive
        -- (data quality guard).
        CASE
            WHEN br.trading_amount IS NOT NULL
                 AND pc.benchmark_non_this_industry_trading_amt IS NOT NULL
                 AND br.trading_amount - pc.benchmark_non_this_industry_trading_amt > 0
            THEN br.trading_amount - pc.benchmark_non_this_industry_trading_amt
            ELSE NULL
        END AS shared_trading_amt
    FROM industry_non_ind pc
    JOIN bench_returns br ON br.date = pc.date
    WHERE pc.non_industry_return_nd IS NOT NULL
      AND br.benchmark_return_nd IS NOT NULL
),
-- Compute metric_value based on attribution_type:
--   'equal'       -> metric_value = hype
--   'trading_amt' -> metric_value = hype * shared_trading_amt (the "amt" variant)
per_industry_metric AS (
    SELECT
        *,
        CASE
            WHEN $3::text = 'equal' THEN hype
            WHEN $3::text = 'trading_amt' THEN hype * shared_trading_amt
            ELSE NULL
        END AS metric_value
    FROM per_industry
    WHERE hype IS NOT NULL
),
industry_label AS (
    SELECT DISTINCT industry_id, industry_label
    FROM stats.sec_classification
    WHERE type = 'index' AND industry_id IS NOT NULL AND industry_id <> ''
      AND industry_label IS NOT NULL
      AND is_industry_not_strategy = TRUE
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY date ORDER BY metric_value DESC NULLS LAST
        ) AS hype_rank,
        ROW_NUMBER() OVER (
            PARTITION BY date ORDER BY metric_value ASC NULLS LAST
        ) AS drain_rank
    FROM per_industry_metric
    WHERE metric_value IS NOT NULL
)
INSERT INTO analysis.industry_hypes_and_drains
    (date, benchmark_code, period_days, weighting, rank_side, rank,
     industry_id, industry_label, metric_value, shared_trading_amt,
     benchmark_return_nd, non_industry_return_nd, benchmark_shared_weight)
SELECT
    r.date,
    $2::text                                                AS benchmark_code,
    {period}::int                                           AS period_days,
    -- Map attribution_type to weighting:
    --   'equal' -> 'equal', 'trading_amt' -> 'amt'
    CASE WHEN $3::text = 'equal' THEN 'equal' ELSE 'amt' END AS weighting,
    side.rank_side,
    side.rank,
    r.industry_id,
    COALESCE(il.industry_label, r.industry_id)              AS industry_label,
    ROUND(r.metric_value::numeric, 6)                       AS metric_value,
    ROUND(r.shared_trading_amt::numeric, 4)                 AS shared_trading_amt,
    ROUND(r.benchmark_return_nd::numeric, 6)                AS benchmark_return_nd,
    ROUND(r.non_industry_return_nd::numeric, 6)             AS non_industry_return_nd,
    ROUND(r.benchmark_shared_weight::numeric, 4)            AS benchmark_shared_weight
FROM ranked r
CROSS JOIN LATERAL (
    VALUES
        ('HYPE'::text,  r.hype_rank),
        ('DRAIN'::text, r.drain_rank)
) AS side(rank_side, rank)
LEFT JOIN industry_label il ON il.industry_id = r.industry_id
WHERE side.rank <= 5
"""


def _build_insert_sql(benchmark_code: str, period: int) -> str:
    """Format the INSERT template for one (benchmark_code, period).

    The attribution_type is passed as a SQL parameter ($3) at execute time,
    NOT format-substituted, to avoid SQL injection and allow the same
    formatted SQL to be reused for both 'equal' and 'trading_amt' calls.
    """
    return _INSERT_SQL_TEMPLATE.format(
        period=period,
        rolling_col=_rolling_col(period),
    )


# Bump work_mem for the window functions + hash aggregate.
SET_WORK_MEM_SQL = "SET work_mem = '512MB'"


# ---------------------------------------------------------------------------
#  Seasonal (monthly) aggregation SQL
# ---------------------------------------------------------------------------

# Aggregates the per-date rankings into per-month rankings.
#
# For each (month, benchmark, period, rank_side, industry):
#   HYPE:  peak_metric_value = MAX(metric_value) over all trading days in the
#          month where this industry was in the top-5 HYPE.
#   DRAIN: peak_metric_value = MIN(metric_value) over all trading days in the
#          month where this industry was in the bottom-5 DRAIN.
#
# Then ranks by peak_metric_value (DESC for HYPE, ASC for DRAIN) and keeps
# the top-5 per (month, benchmark, period, rank_side).
#
# season_qkey format: '2026-08' (year + '-' + zero-padded month).
# season_start/season_end: calendar month boundaries (inclusive).
_SEASONAL_INSERT_SQL = """
WITH per_date AS (
    SELECT
        date,
        EXTRACT(YEAR FROM date)::int    AS season_year,
        EXTRACT(MONTH FROM date)::int   AS season_month,
        to_char(date, 'YYYY-MM')        AS season_qkey,
        (DATE_TRUNC('month', date))::date
                                         AS season_start,
        (DATE_TRUNC('month', date) + INTERVAL '1 month' - INTERVAL '1 day')::date
                                         AS season_end,
        benchmark_code,
        period_days,
        weighting,
        rank_side,
        industry_id,
        industry_label,
        metric_value
    FROM analysis.industry_hypes_and_drains
    WHERE metric_value IS NOT NULL
),
monthly AS (
    SELECT
        season_year,
        season_month,
        season_qkey,
        MIN(season_start)               AS season_start,
        MIN(season_end)                 AS season_end,
        benchmark_code,
        period_days,
        weighting,
        rank_side,
        industry_id,
        MAX(industry_label)             AS industry_label,
        CASE WHEN rank_side = 'HYPE'
             THEN MAX(metric_value)
             ELSE MIN(metric_value)
        END                             AS peak_metric_value
    FROM per_date
    GROUP BY
        season_year,
        season_month,
        season_qkey,
        benchmark_code,
        period_days,
        weighting,
        rank_side,
        industry_id
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY season_qkey, benchmark_code, period_days, weighting, rank_side
            ORDER BY
                CASE WHEN rank_side = 'HYPE'
                     THEN peak_metric_value END DESC NULLS LAST,
                CASE WHEN rank_side = 'DRAIN'
                     THEN peak_metric_value END ASC NULLS LAST
        ) AS rank
    FROM monthly
)
INSERT INTO analysis.industry_hypes_seasonal
    (season_qkey, season_year, season_month, season_start, season_end,
     benchmark_code, period_days, weighting, rank_side, rank,
     industry_id, industry_label, peak_metric_value)
SELECT
    season_qkey, season_year, season_month, season_start, season_end,
    benchmark_code, period_days, weighting, rank_side, rank,
    industry_id, industry_label,
    ROUND(peak_metric_value::numeric, 6)
FROM ranked
WHERE rank <= 5
"""


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

async def run_hypes_and_drains(
    conn,
    *,
    force: bool = True,
) -> None:
    """Run the industry hypes & drains ranking pipeline.

    Reuses the caller's DB connection. Truncates the table first (force is
    the default — the table is small and cheap to fully recompute).

    Pipeline
      1. Guard: bail out if industry_attributions has no broad-market rows.
      2. Warn (not abort) if the 120d column has no data.
      3. Fetch all broad-market benchmark codes.
      4. Truncate analysis.industry_hypes_and_drains.
      5. For each (benchmark_code, period, weighting): run the INSERT...SELECT.
         weighting 'equal' uses attribution_type='equal' (metric_value=hype);
         weighting 'amt' uses attribution_type='trading_amt'
         (metric_value=hype * shared_trading_amt).
      6. Upsert analysis.analysis_identity.
      7. Sanity summary by (benchmark_code, period, weighting).

    Args:
      force: when True (default), truncate + recompute.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  INDUSTRY HYPES & DRAINS (internal step of industry_sentiments)",
          flush=True)
    print("=" * 78, flush=True)

    # ---- Step 1: guard -----------------------------------------------
    n_src = await conn.fetchval(COUNT_SOURCE_SQL)
    if not n_src:
        print("\n[hd1/7] industry_attributions has no broad-market rows "
              "with 255d data — nothing to rank. Skipping "
              "hypes_and_drains step.", flush=True)
        return
    print(f"\n[hd1/7] Source analysis.industry_attributions: "
          f"{n_src:,} broad-market rows with 255d data.", flush=True)

    # ---- Step 2: 120d column check (warn, don't abort) ---------------
    n_120 = await conn.fetchval(COUNT_120D_SQL)
    if not n_120:
        print("      WARNING: benchmark_non_this_industry_rolling_120days_"
              "price is NULL for all broad-market benchmarks. The "
              "attributions step's 120d backfill has not run yet — "
              "period=120 rows will be skipped.", flush=True)
    else:
        print(f"      120d column populated ({n_120:,} rows).", flush=True)

    # ---- Step 3: fetch broad-market benchmark codes -----------------
    benchmark_codes = [r["benchmark_code"] for r in await conn.fetch(
        BROAD_MARKET_BENCHMARKS_SQL
    )]
    print(f"\n[hd2/7] Found {len(benchmark_codes)} broad-market benchmarks: "
          f"{', '.join(benchmark_codes)}", flush=True)

    # ---- Step 4: truncate -------------------------------------------
    print(f"\n[hd3/7] Truncating {TABLE} (full recompute)...", flush=True)
    await truncate_table_async(conn, TABLE)

    # ---- Step 5: per-(benchmark, period, weighting) INSERT ----------
    # Loop over weighting types: 'equal' (attribution_type='equal',
    # metric_value = hype) and 'amt' (attribution_type='trading_amt',
    # metric_value = hype * shared_trading_amt). The weighting column in
    # industry_hypes_and_drains maps directly: 'equal' -> 'equal',
    # 'trading_amt' -> 'amt'.
    WEIGHTINGS = ('equal', 'amt')
    await conn.execute(SET_WORK_MEM_SQL)
    n_total = 0
    for bm_code in benchmark_codes:
        for period in PERIODS:
            for weighting in WEIGHTINGS:
                attribution_type = (
                    'trading_amt' if weighting == 'amt' else 'equal'
                )
                t_iter = time.time()
                sql = _build_insert_sql(bm_code, period)
                status = await conn.execute(
                    sql, period, bm_code, attribution_type
                )
                n_iter = _parse_insert_count(status)
                n_total += n_iter
                print(f"  [hd4/7] {bm_code} period={period:>3d}d "
                      f"{weighting:5s}: inserted {n_iter:>7,} rows "
                      f"({time.time() - t_iter:.1f}s)", flush=True)
                del status, n_iter, sql
                gc.collect()
    gc.collect()
    print(f"  total: {n_total:,} rows inserted across "
          f"{len(benchmark_codes)} benchmarks x {len(PERIODS)} periods "
          f"x {len(WEIGHTINGS)} weightings", flush=True)

    # ---- Step 6: upsert analysis_identity ---------------------------
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME,
        detail_name=ANALYSIS_NAME,
        description=ANALYSIS_DESCRIPTION,
    )

    # ---- Step 7: sanity summary -------------------------------------
    summary = await conn.fetch("""
        SELECT benchmark_code, period_days, weighting,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT industry_id) AS n_industries,
               MIN(date) AS first_date,
               MAX(date) AS last_date,
               ROUND(AVG(metric_value), 6) AS avg_metric
        FROM analysis.industry_hypes_and_drains
        GROUP BY benchmark_code, period_days, weighting
        ORDER BY benchmark_code, period_days, weighting
        LIMIT 60
    """)
    print("\n      Summary by (benchmark, period, weighting) [first 60]:",
          flush=True)
    for r in summary:
        print(f"        {r['benchmark_code']} {r['period_days']:>3d}d "
              f"{r['weighting']:5s}: "
              f"{r['n_rows']:>7,} rows . "
              f"{r['n_industries']:>3} ind . "
              f"{r['first_date']} -> {r['last_date']} . "
              f"avg_metric={r['avg_metric']}", flush=True)

    print(f"\n  hypes_and_drains wall time: {time.time() - t0:.1f}s",
          flush=True)

    # ---- Seasonal (monthly) aggregation -----------------------------
    await run_hypes_and_drains_seasonal(conn)


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


# ---------------------------------------------------------------------------
#  Seasonal (monthly) aggregation pipeline
# ---------------------------------------------------------------------------

async def run_hypes_and_drains_seasonal(conn) -> None:
    """Aggregate per-date rankings into per-month (seasonal) rankings.

    Truncates analysis.industry_hypes_seasonal, then inserts one row per
    (season_qkey, benchmark_code, period_days, rank_side, rank) — 10 rows
    per (month, benchmark, period): 5 HYPE + 5 DRAIN.

    Ranking method:
      HYPE:  peak_metric_value = MAX(metric_value) in the month.
      DRAIN: peak_metric_value = MIN(metric_value) in the month.
    Industries are ranked by peak_metric_value (DESC for HYPE, ASC for
    DRAIN) and the top-5 per side are kept.

    Must be called AFTER run_hypes_and_drains has populated the per-date
    table.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  INDUSTRY HYPES & DRAINS — SEASONAL (monthly) aggregation",
          flush=True)
    print("=" * 78, flush=True)

    # ---- Truncate ----------------------------------------------------
    print(f"\n[hd-s1/3] Truncating {SEASONAL_TABLE}...", flush=True)
    await truncate_table_async(conn, SEASONAL_TABLE)

    # ---- Insert ------------------------------------------------------
    print("[hd-s2/3] Aggregating per-date rankings into monthly "
          "rankings...", flush=True)
    await conn.execute(SET_WORK_MEM_SQL)
    status = await conn.execute(_SEASONAL_INSERT_SQL)
    n_inserted = _parse_insert_count(status)
    print(f"  inserted {n_inserted:,} seasonal ranking rows "
          f"({time.time() - t0:.1f}s)", flush=True)

    # ---- Summary -----------------------------------------------------
    summary = await conn.fetch("""
        SELECT
            benchmark_code,
            period_days,
            COUNT(DISTINCT season_qkey)  AS n_seasons,
            COUNT(*)                     AS n_rows,
            COUNT(DISTINCT industry_id)  AS n_industries,
            MIN(season_qkey)             AS first_season,
            MAX(season_qkey)             AS last_season
        FROM analysis.industry_hypes_seasonal
        GROUP BY benchmark_code, period_days
        ORDER BY benchmark_code, period_days
        LIMIT 30
    """)
    print("\n      Seasonal summary by (benchmark, period) [first 30]:",
          flush=True)
    for r in summary:
        print(f"        {r['benchmark_code']} {r['period_days']:>3d}d: "
              f"{r['n_seasons']:>2} seasons . "
              f"{r['n_rows']:>5,} rows . "
              f"{r['n_industries']:>3} ind . "
              f"{r['first_season']} -> {r['last_season']}", flush=True)

    print(f"\n  seasonal aggregation wall time: {time.time() - t0:.1f}s",
          flush=True)
