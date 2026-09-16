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
  The industry return is the SHARED PORTFOLIO's return (the benchmark
  members that belong to the industry), recovered by inverting the daily
  return-decomposition identity — NOT by inverting the materialized
  rolling_{N}days_price column. Inverting the rolling column amplifies
  its construction artifacts (clamped / NULL->0 daily returns, exp/ln
  compounding) by (1-swf)/swf, which for narrow industries (swf < 1%)
  yields +-hundreds-of-percent noise that flooded both ranking sides
  (fixed 2026-09-13; the daily identity inverts cleanly because the
  non-industry daily price column was built FROM it).

  For each (date, industry, benchmark, period N):
    bench_return_1d       = close[t] / close[t-1] - 1
    non_industry_return_1d = benchmark_non_this_industry_price[t]
                             / close[t-1] - 1
                             (the build materializes the level as
                             price[t] = close[t-1] * (1 + r_t) — dividing
                             by the PREVIOUS BENCHMARK CLOSE recovers r_t
                             exactly; dividing by the industry price's own
                             previous level instead compounds a cross-term
                             that random-walks to ~±20% error over the
                             window and flips industries between sides)
    swf                    = benchmark_shared_weight / 100.0
    shared_return_1d       = (bench_return_1d
                              - (1 - swf) * non_industry_return_1d) / swf
    industry_return_Nd     = 100 * exp(SUM ln(1 + shared_return_1d))
                             over the trailing N trading-day rows - 100
                             (window must be FULL — COUNT = N; daily
                             returns outside (-0.5, 0.5] contribute 0,
                             the same convention as the attributions
                             build's rolling columns)
    benchmark_return_Nd    = benchmark.close[t] / benchmark.close[t-N] - 1
    hype                   = industry_return_Nd - benchmark_return_Nd

  Positive hype = HYPE (industry's shared stocks outperformed the benchmark);
  negative = DRAIN (underperformed).

  Industries with a NULL shared_return_Nd (swf = 0, no overlap with the
  benchmark, or insufficient history for the full N-row window) are
  excluded from ranking.

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

import logging
logger = logging.getLogger(__name__)


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
    "where industry_return_Nd is the shared portfolio's trailing-N-day "
    "return, recovered by inverting the DAILY decomposition identity "
    "(shared_return_1d = (bench_1d - (1-swf)*non_ind_1d) / swf, swf = "
    "benchmark_shared_weight / 100) and compounding over the full N-row "
    "window — never by inverting the materialized rolling column, whose "
    "construction artifacts the 1/swf inversion would amplify into noise. "
    "period_days in {5,20,60,120,255,500} (120 default). Built by "
    "analyze.industry_sentiments.hypes_and_drains (internal step, "
    "truncate-then-recompute). Depends on analysis.industry_attributions "
    "(incl. the daily benchmark_non_this_industry_price column) being "
    "populated first."
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

# Guard: bail out early if the upstream table is empty / missing OR the
# DAILY non-this-industry price column (the raw material for the daily
# identity inversion) has not been populated.
# Filter on attribution_type='trading_amt' to avoid duplicate counting —
# both variants share the same daily columns.
COUNT_SOURCE_SQL = """
    SELECT COUNT(*) AS n
    FROM analysis.industry_attributions ia
    JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
    WHERE sit.is_broad_market = TRUE
      AND ia.attribution_type = 'trading_amt'
      AND ia.benchmark_non_this_industry_price IS NOT NULL
"""

# Per-(benchmark_code, period, attribution_type) INSERT...SELECT.
#
# Builds the full pipeline server-side for ONE (benchmark, period, variant):
#   bench_daily    — per (benchmark, date): close, 1d and N-day returns via
#                    LAG windows ordered by date.
#   industry_daily — per (date, industry): the DAILY non-industry return
#                    from consecutive benchmark_non_this_industry_price
#                    levels (LAG within the industry's own row sequence),
#                    + shared weight + the non-industry trading amount.
#   shared_daily   — invert the DAILY decomposition identity:
#                    bench_1d = swf*shared_1d + (1-swf)*non_ind_1d
#                      -> shared_1d = (bench_1d - (1-swf)*non_ind_1d) / swf
#                    (swf = benchmark_shared_weight / 100; NULL / swf = 0
#                    -> NULL). Inverting DAILY returns is numerically
#                    clean — the non-industry daily price column was
#                    built FROM this identity; inverting the pre-aggregated
#                    rolling_{N}days column instead would amplify its
#                    clamping/compounding artifacts by (1-swf)/swf and
#                    flood the rankings with noise for narrow industries.
#   shared_nd      — compound the shared daily returns over the trailing N
#                    trading-day rows (ROWS BETWEEN N-1 PRECEDING), the
#                    same exp(SUM ln(1+r)) convention as the attributions
#                    build's rolling columns: daily returns outside
#                    (-0.5, 0.5] contribute 0. The window must be FULL
#                    (COUNT = N) or the row gets NULL — a partial window
#                    would understate the compounded return.
#   per_industry   — JOIN shared_nd x bench_daily on date;
#                    hype = shared_return_nd - benchmark_return_nd;
#                    shared_trading_amt = benchmark total trading
#                    - non-industry trading (NULL when non-positive).
#   per_industry_metric — computes metric_value based on attribution_type:
#                    'equal' -> hype; 'trading_amt' -> hype * shared_trading_amt.
#   ranked         — ROW_NUMBER() OVER (PARTITION BY date ORDER BY
#                    metric_value DESC/ASC) for HYPE/DRAIN, filter <= 5.
#
# Parameters:
#   $1 = period N (int)
#   $2 = benchmark_code (text)
#   $3 = attribution_type (text) — 'equal' or 'trading_amt'
# The {period} placeholder is format-substituted; benchmark_code and
# attribution_type are bound parameters (validated from the DB / frozen
# enum) — never format-substituted.
#
# The weighting column in industry_hypes_and_drains is derived from
# attribution_type: 'equal' -> 'equal', 'trading_amt' -> 'amt'.
_INSERT_SQL_TEMPLATE = """
WITH bench_daily AS (
    SELECT
        ib.date,
        ib.close,
        ib.trading_amount,
        LAG(ib.close) OVER w AS bench_prev_close,
        ib.close / NULLIF(LAG(ib.close) OVER w, 0) - 1.0
            AS bench_return_1d,
        CASE
            WHEN LAG(ib.close, $1::int) OVER w IS NOT NULL
                 AND LAG(ib.close, $1::int) OVER w != 0
            THEN ib.close / LAG(ib.close, $1::int) OVER w - 1.0
            ELSE NULL
        END AS benchmark_return_nd
    FROM stats.index_basic_stats ib
    WHERE ib.code = $2::text
      AND ib.close IS NOT NULL
    WINDOW w AS (ORDER BY ib.date)
),
industry_daily AS (
    SELECT
        ia.date,
        ia.industry_id,
        ia.benchmark_shared_weight,
        ia.benchmark_non_this_industry_price,
        ia.benchmark_non_this_industry_trading_amt
    FROM analysis.industry_attributions ia
    WHERE ia.benchmark_code = $2::text
      AND ia.attribution_type = $3::text
      AND ia.benchmark_non_this_industry_price IS NOT NULL
),
shared_daily AS (
    -- Invert the DAILY decomposition identity (see the template comment
    -- block above). NULL on any leg or swf = 0 -> NULL.
    --
    -- The non-industry DAILY return must be recovered EXACTLY as
    --   non_industry_return_1d = price[t] / bench_close[t-1] - 1
    -- because the build materializes the level as
    --   price[t] = bench_close[t-1] * (1 + non_industry_return_t).
    -- Ratios of CONSECUTIVE LEVELS (price[t]/price[t-1]) instead carry a
    -- (1+bench_ret[t-1])/(1+non_ind_ret[t-1]) cross-term whose
    -- compounding random-walks to ~+-20% error in the trailing-N-day
    -- shared return (weight-independent), flipping industries between
    -- the HYPE and DRAIN sides.
    SELECT
        d.date,
        d.industry_id,
        d.benchmark_shared_weight,
        CASE
            WHEN d.benchmark_shared_weight IS NULL
                 OR d.benchmark_shared_weight = 0
                 OR br.bench_prev_close IS NULL
                 OR br.bench_prev_close = 0
                 OR d.benchmark_non_this_industry_price IS NULL THEN NULL
            ELSE (br.bench_return_1d
                  - (1.0 - d.benchmark_shared_weight / 100.0)
                    * (d.benchmark_non_this_industry_price
                       / br.bench_prev_close - 1.0))
                 / (d.benchmark_shared_weight / 100.0)
        END AS shared_return_1d,
        d.benchmark_non_this_industry_trading_amt
    FROM industry_daily d
    JOIN bench_daily br ON br.date = d.date
),
shared_nd AS (
    SELECT
        sd.date,
        sd.industry_id,
        sd.benchmark_shared_weight,
        CASE WHEN COUNT(sd.shared_return_1d) OVER wnd = $1::int
             THEN 100.0 * exp(
                      SUM(CASE
                          WHEN sd.shared_return_1d > -0.5
                               AND sd.shared_return_1d <= 0.5
                          THEN ln(1.0 + sd.shared_return_1d)
                          ELSE 0
                      END) OVER wnd
                  ) - 100.0
            ELSE NULL
        END AS shared_return_nd,
        sd.benchmark_non_this_industry_trading_amt
    FROM shared_daily sd
    WINDOW wnd AS (
        PARTITION BY sd.industry_id
        ORDER BY sd.date
        ROWS BETWEEN ($1::int - 1) PRECEDING AND CURRENT ROW
    )
),
per_industry AS (
    SELECT
        bd.date,
        sn.industry_id,
        sn.benchmark_shared_weight,
        bd.benchmark_return_nd,
        -- The shared portfolio's trailing-N-day return (fractional):
        -- the "industry return" the hype is measured on.
        sn.shared_return_nd / 100.0 AS industry_return_nd,
        -- hype = industry_return_nd - benchmark_return_nd
        sn.shared_return_nd / 100.0 - bd.benchmark_return_nd AS hype,
        CASE
            WHEN bd.trading_amount IS NOT NULL
                 AND sn.benchmark_non_this_industry_trading_amt IS NOT NULL
                 AND bd.trading_amount - sn.benchmark_non_this_industry_trading_amt > 0
            THEN bd.trading_amount - sn.benchmark_non_this_industry_trading_amt
            ELSE NULL
        END AS shared_trading_amt
    FROM shared_nd sn
    JOIN bench_daily bd ON bd.date = sn.date
    WHERE sn.shared_return_nd IS NOT NULL
      AND bd.benchmark_return_nd IS NOT NULL
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
     benchmark_return_nd, industry_return_nd, benchmark_shared_weight)
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
    ROUND(r.industry_return_nd::numeric, 6)                 AS industry_return_nd,
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
    return _INSERT_SQL_TEMPLATE.format(period=period)


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
      1. Guard: bail out if industry_attributions has no broad-market rows
         with daily non-industry price data.
      2. Fetch all broad-market benchmark codes.
      3. Truncate analysis.industry_hypes_and_drains.
      4. For each (benchmark_code, period, weighting): run the INSERT...SELECT.
         weighting 'equal' uses attribution_type='equal' (metric_value=hype);
         weighting 'amt' uses attribution_type='trading_amt'
         (metric_value=hype * shared_trading_amt).
      5. Upsert analysis.analysis_identity.
      6. Sanity summary by (benchmark_code, period, weighting).
      7. Seasonal (monthly) aggregation.

    Args:
      force: when True (default), truncate + recompute.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  INDUSTRY HYPES & DRAINS (internal step of industry_sentiments)")
    logger.info("=" * 78)

    # ---- Step 1: guard -----------------------------------------------
    n_src = await conn.fetchval(COUNT_SOURCE_SQL)
    if not n_src:
        logger.info("\n[hd1/6] industry_attributions has no broad-market rows "
              "with daily non-industry price data — nothing to rank. "
              "Skipping hypes_and_drains step.")
        return
    logger.info(f"\n[hd1/6] Source analysis.industry_attributions: "
          f"{n_src:,} broad-market rows with daily non-industry price data.")

    # ---- Step 2: fetch broad-market benchmark codes -----------------
    benchmark_codes = [r["benchmark_code"] for r in await conn.fetch(
        BROAD_MARKET_BENCHMARKS_SQL
    )]
    logger.info(f"\n[hd2/6] Found {len(benchmark_codes)} broad-market benchmarks: "
          f"{', '.join(benchmark_codes)}")

    # ---- Step 4: truncate -------------------------------------------
    logger.info(f"\n[hd3/6] Truncating {TABLE} (full recompute)...")
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
                logger.info(f"  [hd4/6] {bm_code} period={period:>3d}d "
                      f"{weighting:5s}: inserted {n_iter:>7,} rows "
                      f"({time.time() - t_iter:.1f}s)")
                del status, n_iter, sql
                gc.collect()
    gc.collect()
    logger.info(f"  total: {n_total:,} rows inserted across "
          f"{len(benchmark_codes)} benchmarks x {len(PERIODS)} periods "
          f"x {len(WEIGHTINGS)} weightings")

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
    logger.info("\n      Summary by (benchmark, period, weighting) [first 60]:")
    for r in summary:
        logger.info(f"        {r['benchmark_code']} {r['period_days']:>3d}d "
              f"{r['weighting']:5s}: "
              f"{r['n_rows']:>7,} rows . "
              f"{r['n_industries']:>3} ind . "
              f"{r['first_date']} -> {r['last_date']} . "
              f"avg_metric={r['avg_metric']}")

    logger.info(f"\n  hypes_and_drains wall time: {time.time() - t0:.1f}s")

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
    logger.info("\n" + "=" * 78)
    logger.info("  INDUSTRY HYPES & DRAINS — SEASONAL (monthly) aggregation")
    logger.info("=" * 78)

    # ---- Truncate ----------------------------------------------------
    logger.info(f"\n[hd-s1/3] Truncating {SEASONAL_TABLE}...")
    await truncate_table_async(conn, SEASONAL_TABLE)

    # ---- Insert ------------------------------------------------------
    logger.info("[hd-s2/3] Aggregating per-date rankings into monthly "
          "rankings...")
    await conn.execute(SET_WORK_MEM_SQL)
    status = await conn.execute(_SEASONAL_INSERT_SQL)
    n_inserted = _parse_insert_count(status)
    logger.info(f"  inserted {n_inserted:,} seasonal ranking rows "
          f"({time.time() - t0:.1f}s)")

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
    logger.info("\n      Seasonal summary by (benchmark, period) [first 30]:")
    for r in summary:
        logger.info(f"        {r['benchmark_code']} {r['period_days']:>3d}d: "
              f"{r['n_seasons']:>2} seasons . "
              f"{r['n_rows']:>5,} rows . "
              f"{r['n_industries']:>3} ind . "
              f"{r['first_season']} -> {r['last_season']}")

    logger.info(f"\n  seasonal aggregation wall time: {time.time() - t0:.1f}s")
