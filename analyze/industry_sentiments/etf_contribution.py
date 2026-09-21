"""Internal ETF contribution step for analyze.industry_sentiments.

Aggregates stats.index_exts.total_etf_trading_amount (aggregate ETF
turnover per tracked index, built by builds.index exts phase) to the
industry level, producing analysis.industry_etf_contribution with one
row per (date, industry_id, pool_size).

AGGREGATION
  industry_etf_trading_amount = SUM(total_etf_trading_amount) across
  member indices in the industry (formerly relayed via
  stats.cross_stats.code_etf_trading_amount — that stored copy was
  consolidated away 2026-09-07 as a verbatim index_exts duplicate, so
  the aggregation now joins index_exts directly).

  Cross-sectional count across member indices (same GROUP BY):
    industry_etf_count = COUNT(DISTINCT code) with non-NULL ETF amount.

  NOTE: an ETF that tracks multiple member indices in the SAME industry would
  be counted once per tracked index. In practice most ETFs track exactly ONE
  index, so double-counting is rare. This mirrors the
  stats.industry_basic_stats.total_trading_amount pattern (SUM across
  member indices, built by builds.industry).

POOL_SIZE
  Same classification as builds.industry (stats.industry_basic_stats):
    small = stock_num < 51, mid = 51-180, large = > 180, all = every member.
  stock_num = COUNT(DISTINCT stock_code) from stats.sec_composition (same
  as builds.industry __main__.py POOL_UNION_TEMP_SQL).

MA5
  industry_etf_trading_amount_ma5 = 5-trading-day moving average, computed in
  pandas via rolling(5).mean(min_periods=1) per (industry_id, pool_size) group.

MA20
  industry_etf_trading_amount_ma20 = 20-trading-day moving average, computed
  in pandas via rolling(20).mean(min_periods=1) per (industry_id, pool_size)
  group. Longer-window smoother than MA5, exposed by the UI "Trading Amt" MA
  selector.

IMPLEMENTATION
  The aggregation is pure SQL (CTE chain → GROUP BY). The MA5 / MA20 are
  computed in pandas after loading the SQL result. Force mode: TRUNCATE then
  INSERT. Incremental mode: date filter + ON CONFLICT DO UPDATE.

DEPENDENCY
  Depends on stats.index_exts (builds.index exts phase) being populated
  first. If no index has a non-NULL total_etf_trading_amount, the step
  exits gracefully.

This module is an INTERNAL step of analyze.industry_sentiments — it is
invoked from __main__.py after the attributions step, reusing the same DB
connection. It is NOT a standalone runnable.
"""
from __future__ import annotations

import datetime
import time
from typing import Optional, Set

import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    copy_or_upsert_split_async,
    rec_cols,
)
from _common.df_utils import epoch_col_to_dt64
from analyze._common import (
    grouped_rolling_agg,
    sanitize_for_db_insert,
    upsert_analysis_identity,
)

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_etf_contribution"
ANALYSIS_NAME = "industry_etf_contribution"
ANALYSIS_DESCRIPTION = (
    "Per-(date, industry_id, pool_size) aggregate ETF trading turnover. "
    "industry_etf_trading_amount = SUM(total_etf_trading_amount) across "
    "member indices from stats.index_exts. industry_etf_count = COUNT of "
    "member indices with non-NULL ETF amount. Each member index "
    "contributes its aggregate ETF turnover (precomputed in "
    "stats.index_exts). pool_size: small (stock_num<51), mid (51-180), "
    "large (>180), all (every member). "
    "industry_etf_trading_amount_ma5 = 5-day MA, "
    "industry_etf_trading_amount_ma20 = 20-day MA. Built by "
    "analyze.industry_sentiments.etf_contribution (internal step, "
    "truncate-then-recompute). Depends on stats.index_exts (builds.index "
    "exts phase) being populated first."
)


# ---------------------------------------------------------------------------
#  SQL
# ---------------------------------------------------------------------------

# Guard: bail out early if the upstream table has no ETF amount rows.
# Source: stats.index_exts (built by builds.index exts phase; the former
# relay via stats.cross_stats.code_etf_trading_amount was consolidated
# away 2026-09-07 as a verbatim duplicate).
COUNT_SOURCE_SQL = """
    SELECT COUNT(*) AS n
    FROM stats.index_exts
    WHERE total_etf_trading_amount IS NOT NULL
"""

# The full aggregation, server-side.
#
# CTE chain:
#   etf_amt       — (code, date, total_etf_trading_amount) from
#                   stats.index_exts. One row per (code, date) by the
#                   table's PK — no DISTINCT needed. {date_filter} applies
#                   HERE ONLY (incremental mode targets specific dates).
#   index_info    — per-index: industry_id, industry_label, stock_num (from
#                   COUNT(DISTINCT stock_code) in sec_composition — same as
#                   industry_sentiments POOL_UNION_TEMP_SQL).
#   pool_rows     — one row per (index_code, pool_size): 'all' for every
#                   index, plus the specific pool (small/mid/large) based on
#                   stock_num. UNION ALL expands each index into 1-2 rows.
#   Final SELECT  — JOIN etf_amt with pool_rows. GROUP BY
#                   (date, industry_id, pool_size). Computes:
#                     industry_etf_count = COUNT(DISTINCT code) [with non-NULL
#                                          ETF trading amount]
#                     industry_etf_trading_amount = SUM(total_etf_trading_amount)
#
# {date_filter} placeholder: "" for full, "AND ie.date = ANY($1::date[])" for
# incremental.
_AGGREGATE_SQL = """
WITH etf_amt AS (
    SELECT
        ie.code,
        ie.date,
        ie.total_etf_trading_amount AS code_etf_trading_amount
    FROM stats.index_exts ie
    WHERE ie.total_etf_trading_amount IS NOT NULL
      {date_filter}
),
index_info AS (
    SELECT
        cls.code,
        cls.industry_id,
        COALESCE(cls.industry_label, cls.industry_id) AS industry_label,
        sc.stock_num
    FROM (
        SELECT DISTINCT code, industry_id, industry_label
        FROM stats.sec_classification
        WHERE type = 'index'
          AND is_active = TRUE
          AND is_industry_not_strategy = TRUE
          AND industry_id IS NOT NULL
          AND industry_id <> ''
    ) cls
    LEFT JOIN LATERAL (
        SELECT COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
          AND code = cls.code
    ) sc ON true
),
-- Generate pool_size rows: 'all' for every index, plus the specific pool.
pool_rows AS (
    SELECT code, industry_id, industry_label, 'all' AS pool_size
    FROM index_info
    UNION ALL
    SELECT code, industry_id, industry_label, 'small' AS pool_size
    FROM index_info WHERE stock_num IS NOT NULL AND stock_num < 51
    UNION ALL
    SELECT code, industry_id, industry_label, 'mid' AS pool_size
    FROM index_info WHERE stock_num IS NOT NULL
                       AND stock_num >= 51 AND stock_num <= 180
    UNION ALL
    SELECT code, industry_id, industry_label, 'large' AS pool_size
    FROM index_info WHERE stock_num IS NOT NULL AND stock_num > 180
)
SELECT
    extract(epoch from ea.date)::float8 AS date,
    pr.industry_id,
    pr.industry_label,
    pr.pool_size,
    COUNT(DISTINCT ea.code) AS industry_etf_count,
    SUM(ea.code_etf_trading_amount) AS industry_etf_trading_amount
FROM etf_amt ea
JOIN pool_rows pr ON pr.code = ea.code
GROUP BY ea.date, pr.industry_id, pr.industry_label, pr.pool_size
"""

# Full recompute — no date filter.
AGGREGATE_SQL_FULL = _AGGREGATE_SQL.format(date_filter="")

# Incremental — date filter on the source table.
AGGREGATE_SQL_INCREMENTAL = _AGGREGATE_SQL.format(
    date_filter="AND ie.date = ANY($1::date[])"
)


# ---------------------------------------------------------------------------
#  Pipeline
# ---------------------------------------------------------------------------

async def run_etf_contribution(
    conn,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    force: bool = False,
) -> None:
    """Run the industry ETF contribution aggregation pipeline.

    Reuses the caller's DB connection (does not open/close its own) so the
    sentiments + correlations + attributions + etf_contribution steps form
    a single atomic-ish batch.

    Pipeline
      1. Guard: if stats.index_exts has no non-NULL
         total_etf_trading_amount rows, exit gracefully.
      2. Force mode: TRUNCATE analysis.industry_etf_contribution.
      3. SQL: aggregate index_exts ETF turnover per (date, industry_id,
         pool_size).
      4. pandas: compute 5-day and 20-day MA per (industry_id, pool_size)
         group.
      5. Upsert into analysis.industry_etf_contribution.
      6. Register in analysis.analysis_identity.

    Args:
      target_dates: when non-empty (and force=False), only rows whose date
        is in this set are upserted (incremental mode).
      force: when True, truncate the table first and recompute all rows.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  INDUSTRY ETF CONTRIBUTION (internal step of industry_sentiments)")
    logger.info("=" * 78)

    incremental = (not force
                   and target_dates is not None
                   and len(target_dates) > 0)
    if force:
        logger.info("    mode: FORCE (full recompute)")
    elif incremental:
        logger.info(f"    mode: incremental ({len(target_dates)} target dates)")

    # ---- Step 1: guard — check upstream availability ----------------
    n_src = await conn.fetchval(COUNT_SOURCE_SQL)
    if not n_src:
        logger.info("\n[e1/5] stats.index_exts has no non-NULL "
              "total_etf_trading_amount rows — nothing to materialize. "
              "Skipping etf_contribution step.")
        return
    logger.info(f"\n[e1/5] Source: {n_src:,} index_exts rows with non-NULL "
          f"total_etf_trading_amount.")

    # ---- Step 2: truncate (full recompute only) ---------------------
    if not incremental:
        logger.info(f"\n[e2/5] Truncating {TABLE} (full recompute)...")
        await truncate_table_async(conn, TABLE)
    else:
        logger.info(f"\n[e2/5] Incremental mode — no truncate "
              f"(ON CONFLICT DO UPDATE handles dedup).")

    # ---- Step 3: SQL aggregation ------------------------------------
    logger.info("\n[e3/5] Aggregating index_exts total_etf_trading_amount "
          "per (date, industry_id, pool_size)...")
    t_sql = time.time()
    if incremental:
        sorted_dates = sorted(target_dates)
        rows = await conn.fetch(AGGREGATE_SQL_INCREMENTAL, sorted_dates)
    else:
        rows = await conn.fetch(AGGREGATE_SQL_FULL)
    logger.info(f"    -> {len(rows):,} aggregated rows "
          f"({time.time() - t_sql:.1f}s)")

    if not rows:
        logger.info("    -> no data; skipping upsert.")
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name=ANALYSIS_NAME,
            description=ANALYSIS_DESCRIPTION,
        )
        logger.info(f"\n  etf_contribution wall time: {time.time() - t0:.1f}s")
        return

    # ---- Step 4: pandas MA5 / MA20 ----------------------------------
    logger.info("\n[e4/5] Computing 5-day & 20-day MA per (industry_id, "
          "pool_size)...")
    t_ma = time.time()
    # Whole-column extraction via the shared helper (C-level itemgetter
    # map + one positional unpack — never a per-row python loop; see
    # project convention for DB Record -> column extraction). dtypes are
    # then assigned with single vectorized casts: count as nullable
    # Int64 (COUNT bigint, asyncpg-safe), amount float (NUMERIC Decimal
    # -> float64).
    df = pd.DataFrame(rec_cols(rows))
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df["industry_etf_count"] = df["industry_etf_count"].astype("Int64")
    df["industry_etf_trading_amount"] = (
        df["industry_etf_trading_amount"].astype(float)
    )
    # Sort by (industry_id, pool_size, date) so rolling window is correct.
    df = df.sort_values(["industry_id", "pool_size", "date"]).reset_index(drop=True)
    # Compute MA per (industry_id, pool_size) group via the shared
    # grouped_rolling_agg helper (Cython-compiled groupby.rolling().mean()
    # — no Python lambda callback per group). min_periods=1 so the first
    # N-1 days use a partial average. sort=False because df is already
    # sorted by the group keys.
    grp_keys = ["industry_id", "pool_size"]
    df["industry_etf_trading_amount_ma5"] = grouped_rolling_agg(
        df, grp_keys, "industry_etf_trading_amount",
        window=5, min_periods=1, agg="mean", sort=False,
    )
    df["industry_etf_trading_amount_ma20"] = grouped_rolling_agg(
        df, grp_keys, "industry_etf_trading_amount",
        window=20, min_periods=1, agg="mean", sort=False,
    )
    # Convert date to python datetime.date for asyncpg — done INSIDE
    # sanitize_for_db_insert below (date_cols branch, ONE host numpy
    # pass). Keeping the column datetime64 through the frame's life is
    # the cudf.pandas convention: an object-date column poisons every
    # subsequent op on the frame (MixedTypeError fallbacks per access).
    logger.info(f"    -> MA5 / MA20 computed for {len(df):,} rows "
          f"({time.time() - t_ma:.1f}s)")

    # ---- Step 5: upsert ---------------------------------------------
    logger.info(f"\n[e5/5] Upserting into {TABLE}...")
    # Sanitize the DataFrame for asyncpg upsert via the shared helper:
    # NaN/inf -> None for numeric cols, date -> python date objects
    # (date_cols), non-numeric cols pass through.
    # Replaces the per-row iterrows dict construction with a single
    # vectorized to_dict pass.
    data = sanitize_for_db_insert(
        df,
        numeric_cols=[
            "industry_etf_count",
            "industry_etf_trading_amount",
            "industry_etf_trading_amount_ma5",
            "industry_etf_trading_amount_ma20",
        ],
        date_cols=["date"],
    )
    n_copied, n_upserted = await copy_or_upsert_split_async(
        conn, TABLE, data,
        key_columns=["date", "industry_id", "pool_size"],
    )
    n = n_copied + n_upserted
    via = "COPY" if n_copied > 0 and n_upserted == 0 else \
          f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
          "upsert"
    logger.info(f"    -> inserted {n:,} rows via {via}")

    # ---- Step 6: register in analysis_identity ----------------------
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME,
        detail_name=ANALYSIS_NAME,
        description=ANALYSIS_DESCRIPTION,
    )

    logger.info(f"\n  etf_contribution wall time: {time.time() - t0:.1f}s")
