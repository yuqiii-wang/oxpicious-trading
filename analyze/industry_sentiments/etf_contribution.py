"""Internal ETF contribution step for analyze.industry_sentiments.

Aggregates analysis.sec_alloc_perf_attribution.code_etf_trading_amount to the
industry level, producing analysis.industry_etf_contribution with one row per
(date, industry_id, pool_size).

AGGREGATION
  industry_etf_trading_amount = SUM(code_etf_trading_amount) across member
  indices in the industry (from sec_alloc_perf_attribution where
  sec_type='index'). Each member index's code_etf_trading_amount is the
  aggregate ETF turnover tracking that index (precomputed in
  stats.index_exts.total_etf_trading_amount).

  Cross-sectional count across member indices (same GROUP BY):
    industry_etf_count = COUNT(DISTINCT code) with non-NULL ETF amount.

  NOTE: an ETF that tracks multiple member indices in the SAME industry would
  be counted once per tracked index. In practice most ETFs track exactly ONE
  index, so double-counting is rare. This mirrors the
  industry_sentiments.total_trading_amount pattern.

POOL_SIZE
  Same classification as industry_sentiments:
    small = stock_num < 51, mid = 51-180, large = > 180, all = every member.
  stock_num = COUNT(DISTINCT stock_code) from stats.sec_composition (same as
  industry_sentiments __main__.py POOL_UNION_TEMP_SQL).

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
  Depends on analysis.sec_alloc_perf_attribution being populated first. If
  that table has no index rows with non-NULL code_etf_trading_amount, the
  step exits gracefully.

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
    bulk_upsert_async,
)
from analyze._common import (
    grouped_rolling_agg,
    sanitize_for_db_insert,
    upsert_analysis_identity,
)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.industry_etf_contribution"
ANALYSIS_NAME = "industry_etf_contribution"
ANALYSIS_DESCRIPTION = (
    "Per-(date, industry_id, pool_size) aggregate ETF trading turnover. "
    "industry_etf_trading_amount = SUM(code_etf_trading_amount) across "
    "member indices from analysis.sec_alloc_perf_attribution (sec_type"
    "='index'). industry_etf_count = COUNT of member indices with non-NULL "
    "ETF amount. Each member index contributes its aggregate ETF turnover "
    "(precomputed in stats.index_exts). pool_size: small (stock_num<51), "
    "mid (51-180), large (>180), all (every member). "
    "industry_etf_trading_amount_ma5 = 5-day MA, "
    "industry_etf_trading_amount_ma20 = 20-day MA. Built by "
    "analyze.industry_sentiments.etf_contribution (internal step, "
    "truncate-then-recompute). Depends on "
    "analysis.sec_alloc_perf_attribution being populated first."
)


# ---------------------------------------------------------------------------
#  SQL
# ---------------------------------------------------------------------------

# Guard: bail out early if the upstream table has no index rows with
# non-NULL code_etf_trading_amount.
COUNT_SOURCE_SQL = """
    SELECT COUNT(*) AS n
    FROM analysis.sec_alloc_perf_attribution
    WHERE sec_type = 'index'
      AND code_etf_trading_amount IS NOT NULL
"""

# The full aggregation, server-side.
#
# CTE chain:
#   etf_amt       — DISTINCT (code, date, code_etf_trading_amount) from
#                   sec_alloc_perf_attribution (sec_type='index'). DISTINCT
#                   because the same value appears for every benchmark_code.
#                   {date_filter} applies HERE ONLY (incremental mode targets
#                   specific dates).
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
#                     industry_etf_trading_amount = SUM(code_etf_trading_amount)
#
# {date_filter} placeholder: "" for full, "AND sa.date = ANY($1::date[])" for
# incremental.
_AGGREGATE_SQL = """
WITH etf_amt AS (
    SELECT DISTINCT
        sa.code,
        sa.date,
        sa.code_etf_trading_amount
    FROM analysis.sec_alloc_perf_attribution sa
    WHERE sa.sec_type = 'index'
      AND sa.code_etf_trading_amount IS NOT NULL
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
    ea.date,
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
    date_filter="AND sa.date = ANY($1::date[])"
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
      1. Guard: if sec_alloc_perf_attribution has no index rows with non-NULL
         code_etf_trading_amount, exit gracefully.
      2. Force mode: TRUNCATE analysis.industry_etf_contribution.
      3. SQL: aggregate code_etf_trading_amount per (date, industry_id,
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
    print("\n" + "=" * 78, flush=True)
    print("  INDUSTRY ETF CONTRIBUTION (internal step of industry_sentiments)",
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

    # ---- Step 1: guard — check upstream availability ----------------
    n_src = await conn.fetchval(COUNT_SOURCE_SQL)
    if not n_src:
        print("\n[e1/5] sec_alloc_perf_attribution has no index rows with "
              "non-NULL code_etf_trading_amount — nothing to materialize. "
              "Skipping etf_contribution step.", flush=True)
        return
    print(f"\n[e1/5] Source: {n_src:,} index rows with non-NULL "
          f"code_etf_trading_amount.", flush=True)

    # ---- Step 2: truncate (full recompute only) ---------------------
    if not incremental:
        print(f"\n[e2/5] Truncating {TABLE} (full recompute)...", flush=True)
        await truncate_table_async(conn, TABLE)
    else:
        print(f"\n[e2/5] Incremental mode — no truncate "
              f"(ON CONFLICT DO UPDATE handles dedup).", flush=True)

    # ---- Step 3: SQL aggregation ------------------------------------
    print("\n[e3/5] Aggregating code_etf_trading_amount per "
          "(date, industry_id, pool_size)...", flush=True)
    t_sql = time.time()
    if incremental:
        sorted_dates = sorted(target_dates)
        rows = await conn.fetch(AGGREGATE_SQL_INCREMENTAL, sorted_dates)
    else:
        rows = await conn.fetch(AGGREGATE_SQL_FULL)
    print(f"    -> {len(rows):,} aggregated rows "
          f"({time.time() - t_sql:.1f}s)", flush=True)

    if not rows:
        print("    -> no data; skipping upsert.", flush=True)
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name=ANALYSIS_NAME,
            description=ANALYSIS_DESCRIPTION,
        )
        print(f"\n  etf_contribution wall time: {time.time() - t0:.1f}s",
              flush=True)
        return

    # ---- Step 4: pandas MA5 / MA20 ----------------------------------
    print("\n[e4/5] Computing 5-day & 20-day MA per (industry_id, "
          "pool_size)...", flush=True)
    t_ma = time.time()
    df = pd.DataFrame({
        "date": [r["date"] for r in rows],
        "industry_id": [r["industry_id"] for r in rows],
        "industry_label": [r["industry_label"] for r in rows],
        "pool_size": [r["pool_size"] for r in rows],
        "industry_etf_count": [
            int(r["industry_etf_count"]) if r["industry_etf_count"] is not None else None
            for r in rows
        ],
        "industry_etf_trading_amount": [
            float(r["industry_etf_trading_amount"])
            if r["industry_etf_trading_amount"] is not None else None
            for r in rows
        ],
    })
    # Ensure date is datetime for proper sorting.
    df["date"] = pd.to_datetime(df["date"])
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
    # Convert date back to datetime.date for asyncpg.
    df["date"] = df["date"].dt.date
    print(f"    -> MA5 / MA20 computed for {len(df):,} rows "
          f"({time.time() - t_ma:.1f}s)", flush=True)

    # ---- Step 5: upsert ---------------------------------------------
    print(f"\n[e5/5] Upserting into {TABLE}...", flush=True)
    # Sanitize the DataFrame for asyncpg upsert via the shared helper:
    # NaN/inf -> None for numeric cols, non-numeric cols pass through.
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
    )
    n = await bulk_upsert_async(
        conn, TABLE, data,
        key_columns=["date", "industry_id", "pool_size"],
        batch_size=1000,
    )
    print(f"    -> upserted {n:,} rows", flush=True)

    # ---- Step 6: register in analysis_identity ----------------------
    await upsert_analysis_identity(
        conn,
        name=ANALYSIS_NAME,
        detail_name=ANALYSIS_NAME,
        description=ANALYSIS_DESCRIPTION,
    )

    print(f"\n  etf_contribution wall time: {time.time() - t0:.1f}s", flush=True)
