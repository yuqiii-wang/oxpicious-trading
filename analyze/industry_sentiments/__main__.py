"""Entry point for analyze.industry_sentiments.

Run via ``python -m analyze.industry_sentiments``.

Pipeline
  1. Load all (date, code, industry_id, close, pe, stock_num) from
     index_basic_stats LEFT JOIN index_valuation JOIN sec_classification
     (compositioned indices only), stock_num via LATERAL sec_composition
     latest-snapshot (no date filter — temporal extrapolation).
  2. Rebase each code's close to 100 at its first available close.
  3. Classify pool_size from stock_num (small/mid/large; NULL -> 'all').
  4. Aggregate mean_price/var_price/mean_pe per (date, industry_id, pool).
  5. SEPARATE SQL: total_trading_amount = SUM(stock_basic_stats.trading_amount)
     across the UNION of stocks from member indices' compositions.
  6. Merge + upsert analysis.industry_sentiments.
  7. INTERNAL STEP: run pairwise rolling correlations of industries'
     mean_price series -> analysis.industry_correlations (see
     correlations.py). Reuses the same DB connection. This step used to
     be a standalone analyze.industry_correlations package; it is now an
     internal step because it strictly depends on industry_sentiments
     being populated first.
  8. INTERNAL STEP: aggregate analysis.sec_alloc_perf_attribution
     shared_weight to the industry level -> analysis.industry_attributions
     (see attributions.py). Reuses the same DB connection. Depends on
     analysis.sec_alloc_perf_attribution being populated first (by
     analyze.sec_alloc_perf_attribution); exits gracefully if empty.

Default (incremental) mode:
  Only dates present in stats.index_identity but NOT yet in
  analysis.industry_sentiments are (re)computed and upserted. The rebase
  step still loads full per-code history (needed to find each code's first
  close) but only target-date rows are aggregated and inserted. The
  internal correlations + attributions steps receive the same target_dates
  and similarly compute only for those dates (rolling-correlation context
  is read from the already-populated sentiments table).

--force mode:
  Truncate analysis.industry_sentiments (and the downstream
  industry_correlations + industry_attributions tables) first, then
  recompute and insert all rows.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys
import time
from typing import Optional, Set

# Ensure project root is on sys.path so ``utils`` is importable when run
# directly via ``python -m analyze.industry_sentiments`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from utils.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    bulk_upsert_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    find_missing_analysis_dates,
    add_force_arg,
)

setup_utf8_stdout()

import pandas as pd  # noqa: E402

from analyze.industry_sentiments.config import (  # noqa: E402
    TABLE,
    ANALYSIS_NAME,
    ANALYSIS_DESCRIPTION,
)
from analyze.industry_sentiments.compute import (  # noqa: E402
    rebase_closes,
    aggregate_by_pool,
)
from analyze.industry_sentiments.correlations import (  # noqa: E402
    run_correlations,
    TABLE as CORRELATIONS_TABLE,
)
from analyze.industry_sentiments.attributions import (  # noqa: E402
    run_attributions,
    TABLE as ATTRIBUTIONS_TABLE,
)


# ---------------------------------------------------------------------------
#  SQL queries (kept in __main__ — they are tightly coupled to the async
#  fetch flow and not reused elsewhere).
# ---------------------------------------------------------------------------

# Step 1 — load per-(date, code, industry_id, close, pe, stock_num) rows.
# stock_num is looked up via LATERAL latest-snapshot per code (no date
# filter — temporal extrapolation: the current pool_size classification
# is used as a proxy for historical membership).
LOAD_INDEX_DATA_SQL = """
    WITH stock_counts AS (
        SELECT code,
               COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
        GROUP BY code
    )
    SELECT
        ib.date,
        ib.code,
        sc.industry_id,
        COALESCE(sc.industry_label, sc.industry_id) AS industry_label,
        ib.close,
        iv.pe,
        latest.stock_num
    FROM stats.index_basic_stats ib
    JOIN stats.sec_classification sc
        ON sc.code = ib.code AND sc.type = 'index'
    LEFT JOIN stats.index_valuation iv
        ON iv.code = ib.code AND iv.date = ib.date
    LEFT JOIN LATERAL (
        SELECT stock_num
        FROM stock_counts sc2
        WHERE sc2.code = ib.code
        LIMIT 1
    ) latest ON true
    WHERE sc.industry_id IS NOT NULL
      AND sc.industry_id <> ''
      AND ib.close IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM stats.sec_composition sc3
          WHERE sc3.code = ib.code
            AND sc3.source_type = 'index'
      )
    ORDER BY ib.code, ib.date
"""

# Step 5a — materialize the per-pool union of stocks (latest snapshot per
# index code, no temporal filter) into a temp table. Each (industry, pool,
# stock) appears once.
POOL_UNION_TEMP_SQL = """
    CREATE TEMP TABLE _pu AS
    WITH index_counts AS (
        SELECT code AS index_code,
               COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
        GROUP BY code
    ),
    index_stocks AS (
        SELECT
            ic.stock_num,
            sc.industry_id,
            COALESCE(sc.industry_label, sc.industry_id) AS industry_label,
            sc2.stock_code
        FROM index_counts ic
        JOIN stats.sec_classification sc
            ON sc.code = ic.index_code AND sc.type = 'index'
        JOIN stats.sec_composition sc2
            ON sc2.code = ic.index_code
           AND sc2.source_type = 'index'
        WHERE sc.industry_id IS NOT NULL AND sc.industry_id <> ''
    )
    SELECT DISTINCT industry_id, industry_label,
           'all' AS pool_size, stock_code
    FROM index_stocks
    UNION ALL
    SELECT DISTINCT industry_id, industry_label,
           'small' AS pool_size, stock_code
    FROM index_stocks WHERE stock_num < 51
    UNION ALL
    SELECT DISTINCT industry_id, industry_label,
           'mid' AS pool_size, stock_code
    FROM index_stocks WHERE stock_num >= 51 AND stock_num <= 180
    UNION ALL
    SELECT DISTINCT industry_id, industry_label,
           'large' AS pool_size, stock_code
    FROM index_stocks WHERE stock_num > 180
"""

# Step 5b — final aggregation join: _pu x stock_basic_stats, GROUP BY
# (date, industry_id, pool_size) -> SUM(trading_amount). Each stock
# counted once (union, not sum-per-index). In incremental mode the date
# filter ``AND sbs.date = ANY($1::date[])`` is appended so only target
# dates are aggregated (the _pu temp table is date-independent).
_POOL_AMOUNT_JOIN_SQL_FULL = """
    SELECT
        sbs.date,
        pu.industry_id,
        pu.industry_label,
        pu.pool_size,
        SUM(sbs.trading_amount) AS total_trading_amount
    FROM _pu pu
    JOIN stats.stock_basic_stats sbs
        ON sbs.code = pu.stock_code
    WHERE sbs.trading_amount IS NOT NULL
    GROUP BY sbs.date, pu.industry_id, pu.industry_label, pu.pool_size
"""

_POOL_AMOUNT_JOIN_SQL_INCREMENTAL = """
    SELECT
        sbs.date,
        pu.industry_id,
        pu.industry_label,
        pu.pool_size,
        SUM(sbs.trading_amount) AS total_trading_amount
    FROM _pu pu
    JOIN stats.stock_basic_stats sbs
        ON sbs.code = pu.stock_code
    WHERE sbs.trading_amount IS NOT NULL
      AND sbs.date = ANY($1::date[])
    GROUP BY sbs.date, pu.industry_id, pu.industry_label, pu.pool_size
"""


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Industry sentiments analysis (rebased-to-100 mean/var "
                    "per date x industry x pool_size)."
    )
    add_force_arg(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "ANALYZE INDUSTRY SENTIMENTS "
        "(rebased-to-100 mean/var per date x industry x pool_size)",
        index_table=TABLE,
        mode="FORCE (full recompute)" if args.force
             else "incremental (missing dates only)",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Step 0: determine target dates -------------------------------
        if args.force:
            print("\n[0/6] Force mode: truncating sentiments + downstream "
                  "tables...", flush=True)
            await truncate_table_async(conn, TABLE)
            await truncate_table_async(conn, CORRELATIONS_TABLE)
            await truncate_table_async(conn, ATTRIBUTIONS_TABLE)
            target_dates: Optional[Set[datetime.date]] = None
            print("    -> truncated; will recompute all rows", flush=True)
        else:
            print("\n[0/6] Detecting missing dates "
                  "(source: index_identity vs sentiments table)...",
                  flush=True)
            target_dates = await find_missing_analysis_dates(
                conn, TABLE, ["stats.index_identity"],
            )
            print(f"    -> {len(target_dates)} dates missing from {TABLE}",
                  flush=True)
            if not target_dates:
                print("    -> DB is up to date; nothing to do.", flush=True)
                print_wall_time(t0)
                return

        # ---- Step 1: load all (date, code, industry_id, close) ------------
        # Full history is always loaded — rebase_closes needs each code's
        # FIRST available close. In incremental mode the filtering to
        # target_dates happens AFTER rebase + aggregate.
        print("\n[1/6] Loading (date, code, industry_id, close, pe, "
              "stock_num) from index_basic_stats LEFT JOIN index_valuation "
              "JOIN sec_classification (compositioned indices only), stock_num "
              "via LATERAL sec_composition latest-snapshot (no date filter)...",
              flush=True)
        rows = await conn.fetch(LOAD_INDEX_DATA_SQL)
        print(f"    -> {len(rows):,} rows across "
              f"{len(set((r['industry_id'], r['code']) for r in rows))} "
              f"(industry, code) pairs", flush=True)

        if not rows:
            print("    -> no data; aborting.", flush=True)
            return

        df = pd.DataFrame(
            {
                "date": [r["date"] for r in rows],
                "code": [r["code"] for r in rows],
                "industry_id": [r["industry_id"] for r in rows],
                "industry_label": [r["industry_label"] for r in rows],
                "close": [float(r["close"]) for r in rows],
                "pe": [
                    None if r["pe"] is None else float(r["pe"])
                    for r in rows
                ],
                "stock_num": [
                    None if r["stock_num"] is None else int(r["stock_num"])
                    for r in rows
                ],
            }
        )
        # Drop rows with NaN/zero close (can't rebase from zero).
        df = df[df["close"].notna() & (df["close"] > 0)].copy()

        # ---- Step 2: rebase each code to 100 at its first available close --
        print("\n[2/6] Rebased-to-100 at per-index first available close "
              "(history start)...", flush=True)
        df = rebase_closes(df)
        print(f"    -> rebased {len(df):,} rows across {df['code'].nunique()} "
              f"indices", flush=True)

        # ---- Incremental filter: keep only target_dates for aggregation -----
        if target_dates is not None and len(target_dates) > 0:
            n_before = len(df)
            df = df[df["date"].isin(target_dates)].reset_index(drop=True)
            print(f"    -> incremental filter: {len(df):,} of {n_before:,} rows "
                  f"are in target_dates (rebase context rows dropped)",
                  flush=True)

        # ---- Step 3 + 4: classify pool_size + aggregate -------------------
        print("\n[3/6] Classifying pool_size from stock_num "
              "(small<51, mid 51-180, large >180; NULL->'all' only)...",
              flush=True)
        print("\n[4/6] Aggregating mean_price/var_price/mean_pe "
              "per (date, industry_id, pool_size)...",
              flush=True)
        result = aggregate_by_pool(df)
        print(f"    -> {len(result):,} aggregated rows across "
              f"{result['industry_id'].nunique()} industries x "
              f"{result['pool_size'].nunique()} pool_size slices", flush=True)

        # ---- Step 5: compute total_trading_amount via SQL (union of stocks) --
        print("\n[5/6] Computing total_trading_amount via SQL "
              "(union of stocks across member indices -> SUM stock trading_amount)...",
              flush=True)

        # 5a. Pool unions temp table — latest snapshot per code, no snapshot_date
        t_pool = time.time()
        await conn.execute("DROP TABLE IF EXISTS pg_temp._pu")
        await conn.execute(POOL_UNION_TEMP_SQL)
        # Index for the join on stock_code (lookup into stock_basic_stats)
        await conn.execute(
            "CREATE INDEX _pu_stock ON _pu (stock_code)"
        )
        await conn.execute("ANALYZE _pu")
        n_pu = await conn.fetchval("SELECT COUNT(*) FROM _pu")
        print(f"    [5a] _pu temp table: {n_pu:,} rows "
              f"({time.time() - t_pool:.1f}s)", flush=True)

        # 5b. Final aggregation join — direct _pu x stock_basic_stats.
        # In incremental mode, filter to target_dates so the SQL only
        # aggregates stock amounts for the dates we actually need.
        t_final = time.time()
        if target_dates is not None and len(target_dates) > 0:
            sorted_dates = sorted(target_dates)
            amt_rows = await conn.fetch(
                _POOL_AMOUNT_JOIN_SQL_INCREMENTAL, sorted_dates
            )
            print(f"    [5b] Final join + GROUP BY (incremental, "
                  f"{len(sorted_dates)} target dates): {len(amt_rows):,} rows "
                  f"({time.time() - t_final:.1f}s)", flush=True)
        else:
            amt_rows = await conn.fetch(_POOL_AMOUNT_JOIN_SQL_FULL)
            print(f"    [5b] Final join + GROUP BY (full): {len(amt_rows):,} rows "
                  f"({time.time() - t_final:.1f}s)", flush=True)

        # Cleanup temp table
        await conn.execute("DROP TABLE IF EXISTS pg_temp._pu")

        amt_df = pd.DataFrame(
            {
                "date": [r["date"] for r in amt_rows],
                "industry_id": [r["industry_id"] for r in amt_rows],
                "pool_size": [r["pool_size"] for r in amt_rows],
                "total_trading_amount": [
                    None if r["total_trading_amount"] is None
                    else float(r["total_trading_amount"])
                    for r in amt_rows
                ],
            }
        )

        # ---- Step 6: merge + upsert ---------------------------------------
        print(f"\n[6/6] Merging index-level + stock-level aggregates, "
              f"upserting into {TABLE}...", flush=True)
        result = result.merge(
            amt_df, on=["date", "industry_id", "pool_size"], how="left"
        )
        # Rows without stock amount data get NULL total_trading_amount
        result["total_trading_amount"] = result["total_trading_amount"].where(
            result["total_trading_amount"].notna(), other=None
        )
        n_with_amt = result["total_trading_amount"].notna().sum()
        print(f"    -> {len(result):,} total rows | "
              f"{n_with_amt:,} with total_trading_amount | "
              f"{len(result) - n_with_amt:,} without (NULL)", flush=True)

        data = [
            {
                "date": r["date"],
                "industry_id": r["industry_id"],
                "pool_size": r["pool_size"],
                "industry_label": r["industry_label"],
                "index_count": int(r["index_count"]) if pd.notna(r["index_count"]) else None,
                "mean_price": float(r["mean_price"]) if pd.notna(r["mean_price"]) else None,
                "var_price": float(r["var_price"]) if pd.notna(r["var_price"]) else None,
                "mean_pe": float(r["mean_pe"]) if pd.notna(r["mean_pe"]) else None,
                "total_trading_amount": float(r["total_trading_amount"]) if pd.notna(r["total_trading_amount"]) else None,
            }
            for _, r in result.iterrows()
        ]
        n = await bulk_upsert_async(
            conn, TABLE, data,
            key_columns=["date", "industry_id", "pool_size"],
            batch_size=1000,
        )
        print(f"    -> upserted {n:,} rows", flush=True)

        # ---- Register in analysis.analysis_identity -----------------------
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
        print(f"    -> upserted analysis_identity (name='{ANALYSIS_NAME}')",
              flush=True)

        # ---- Step 7: INTERNAL correlations step --------------------------
        # Pairwise rolling Pearson correlation of industries' mean_price
        # series -> analysis.industry_correlations. Reuses this same
        # connection. See correlations.py for the full pipeline.
        # Passes target_dates so correlations are computed only for missing
        # dates (force flag cascades from the parent).
        await run_correlations(conn, target_dates=target_dates,
                               force=args.force)

        # ---- Step 8: INTERNAL attributions step -------------------------
        # Aggregate analysis.sec_alloc_perf_attribution shared_weight to the
        # industry level -> analysis.industry_attributions. Reuses this same
        # connection. See attributions.py for the full pipeline. Exits
        # gracefully if sec_alloc_perf_attribution is empty.
        await run_attributions(conn, target_dates=target_dates,
                               force=args.force)

        print_wall_time(t0)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
