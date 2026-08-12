"""Entry point for analyze.industry_sentiments.

Run via ``python -m analyze.industry_sentiments``.

Pipeline
  1. Load (date, code, industry_id, close, pe, stock_num) from
     index_basic_stats LEFT JOIN index_valuation JOIN sec_classification
     (compositioned indices only), stock_num via LATERAL sec_composition
     latest-snapshot (no date filter — temporal extrapolation). In
     incremental mode, filters to target_dates PLUS one first-close row
     per code (rebase anchor); in force mode, loads full history.
  2. Rebase each code's close to 100 at its first available close.
  3. Classify pool_size from stock_num (small/mid/large; NULL -> 'all').
  4. Aggregate mean_price/var_price/mean_pe per (date, industry_id, pool).
  5. SEPARATE SQL: total_trading_amount = SUM(stock_liquidity_margin.trading_amount)
     across the UNION of stocks from member indices' compositions.
  6. Merge + upsert analysis.industry_sentiments.
  7. INTERNAL STEP: run pairwise rolling correlations of industries'
     mean_price series -> analysis.industry_correlations (see
     correlations.py). Reuses the same DB connection. This step used to
     be a standalone analyze.industry_correlations package; it is now an
     internal step because it strictly depends on industry_sentiments
     being populated first.
  8. INTERNAL STEP: populate analysis.sec_alloc_perf_attribution (see
     sec_alloc_perf_attribution.run.run_perf_attribution). Reuses the
     same DB connection. This step used to be a standalone
     analyze.sec_alloc_perf_attribution package; it is now an internal
     step because steps 9 + 10 below READ from this table. It manages
     its OWN target_dates (missing from sec_alloc_perf_attribution vs
     stats.index_identity) since its missing dates can differ from
     industry_sentiments' missing dates.
  9. INTERNAL STEP: aggregate analysis.sec_alloc_perf_attribution
     shared_weight to the industry level -> analysis.industry_attributions
     (see attributions.py). Reuses the same DB connection. Depends on
     step 8 (sec_alloc_perf_attribution) being populated first; exits
     gracefully if empty.
  10. INTERNAL STEP: aggregate code_etf_trading_amount to the industry
      level -> analysis.industry_etf_contribution (see etf_contribution.py).
      Reuses the same DB connection. Depends on step 8
      (sec_alloc_perf_attribution) being populated first; exits
      gracefully if no index rows have non-NULL code_etf_trading_amount.

Default (incremental) mode:
  Only dates present in stats.index_identity but NOT yet in
  analysis.industry_sentiments are (re)computed and upserted. Step 1 loads
  only target-date rows PLUS one first-close row per code (the rebase
  anchor) — full per-code history is NOT loaded, which saves ~99% of rows
  when target_dates is small. The internal correlations + attributions +
  etf_contribution steps receive the same target_dates and similarly
  compute only for those dates (rolling-correlation context is read from
  the already-populated sentiments table).

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

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.industry_sentiments`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    get_db_pool_async,
    bulk_upsert_async,
    copy_insert_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    find_missing_analysis_dates,
    filter_rows_to_missing_dates_async,
    add_force_arg,
)

setup_utf8_stdout()

import pandas as pd  # noqa: E402

from analyze._common import (  # noqa: E402
    sanitize_for_db_insert,
    upsert_analysis_identity,
)
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
    needs_rolling_backfill,
    TABLE as ATTRIBUTIONS_TABLE,
)
from analyze.industry_sentiments.etf_contribution import (  # noqa: E402
    run_etf_contribution,
    TABLE as ETF_CONTRIBUTION_TABLE,
)
from analyze.industry_sentiments.hypes_and_drains import (  # noqa: E402
    run_hypes_and_drains,
    TABLE as HYPES_DRAINS_TABLE,
)
from analyze.sec_alloc_perf_attribution.run import (  # noqa: E402
    run_perf_attribution,
)



# ---------------------------------------------------------------------------
#  SQL queries (kept in __main__ — they are tightly coupled to the async
#  fetch flow and not reused elsewhere).
# ---------------------------------------------------------------------------

# Step 1 — load per-(date, code, industry_id, close, pe, stock_num) rows.
# stock_num is looked up via LATERAL latest-snapshot per code (no date
# filter — temporal extrapolation: the current pool_size classification
# is used as a proxy for historical membership).
#
# Two variants share a common template:
#   * FULL          — every (date, code) row. Used in --force mode so the
#                     full rebase + aggregate runs over the complete history.
#   * INCREMENTAL   — only target_dates PLUS each code's FIRST available
#                     close (the rebase anchor). rebase_closes only needs
#                     each code's first close to compute
#                     rebased = (close / first_close) * 100 — intermediate
#                     dates are not needed. Saves ~99% of rows when
#                     target_dates is small. The first_close CTE filters
#                     close > 0 to match pandas' post-load filter
#                     (df = df[df["close"] > 0]) so the anchor matches the
#                     row pandas would pick as "first" after sorting.
_LOAD_INDEX_DATA_SQL_TEMPLATE = """
    WITH stock_counts AS (
        SELECT code,
               COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
        GROUP BY code
    ){first_close_cte}
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
    ) latest ON true{first_close_join}
    WHERE sc.industry_id IS NOT NULL
      AND sc.industry_id <> ''
      AND sc.is_active = TRUE
      AND ib.close IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM stats.sec_composition sc3
          WHERE sc3.code = ib.code
            AND sc3.source_type = 'index'
      ){date_filter}
    ORDER BY ib.code, ib.date
"""

LOAD_INDEX_DATA_SQL_FULL = _LOAD_INDEX_DATA_SQL_TEMPLATE.format(
    first_close_cte="",
    first_close_join="",
    date_filter="",
)

LOAD_INDEX_DATA_SQL_INCREMENTAL = _LOAD_INDEX_DATA_SQL_TEMPLATE.format(
    first_close_cte=""",
    first_close AS (
        SELECT code, MIN(date) AS first_date
        FROM stats.index_basic_stats
        WHERE close IS NOT NULL AND close > 0
        GROUP BY code
    )""",
    first_close_join="""
    JOIN first_close fc ON fc.code = ib.code""",
    date_filter="""
      AND (ib.date = ANY($1::date[]) OR ib.date = fc.first_date)""",
)

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
          AND sc.is_active = TRUE
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

# Step 5b — final aggregation join: _pu x stock_liquidity_margin, GROUP BY
# (date, industry_id, pool_size) -> SUM(trading_amount). Each stock
# counted once (union, not sum-per-index). In incremental mode the date
# filter ``AND slm.date = ANY($1::date[])`` is appended so only target
# dates are aggregated (the _pu temp table is date-independent).
#
# NOTE: trading_amount was moved from stock_basic_stats to stock_liquidity_margin
# (mirrors etf_liquidity_margin). The JOIN target changed accordingly.
_POOL_AMOUNT_JOIN_SQL_FULL = """
    SELECT
        slm.date,
        pu.industry_id,
        pu.industry_label,
        pu.pool_size,
        SUM(slm.trading_amount) AS total_trading_amount
    FROM _pu pu
    JOIN stats.stock_liquidity_margin slm
        ON slm.code = pu.stock_code
    WHERE slm.trading_amount IS NOT NULL
    GROUP BY slm.date, pu.industry_id, pu.industry_label, pu.pool_size
"""

_POOL_AMOUNT_JOIN_SQL_INCREMENTAL = """
    SELECT
        slm.date,
        pu.industry_id,
        pu.industry_label,
        pu.pool_size,
        SUM(slm.trading_amount) AS total_trading_amount
    FROM _pu pu
    JOIN stats.stock_liquidity_margin slm
        ON slm.code = pu.stock_code
    WHERE slm.trading_amount IS NOT NULL
      AND slm.date = ANY($1::date[])
    GROUP BY slm.date, pu.industry_id, pu.industry_label, pu.pool_size
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
            await truncate_table_async(conn, HYPES_DRAINS_TABLE)
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
                # Even when sentiments is up to date, sec_alloc_perf_attribution
                # (an independent producer sourcing from index_identity +
                # sec_composition + index_exts) may have missing dates, and the
                # attributions table might need a rolling-column backfill —
                # e.g., after adding benchmark_non_this_industry_rolling_*
                # columns via ALTER TABLE. The incremental Step 5 date filter
                # would miss historical dates, so trigger a FULL backfill.
                # Run perf_attribution FIRST so the backfill + downstream
                # aggregations read a current sec_alloc_perf_attribution.
                await run_perf_attribution(conn, force=False)
                if await needs_rolling_backfill(conn):
                    print("    -> sentiments up to date, but "
                          "industry_attributions needs rolling-column "
                          "backfill — running attributions backfill...",
                          flush=True)
                    await run_attributions(conn, backfill=True)
                    # Backfill just refreshed the rolling price columns (incl.
                    # 120d) — recompute hypes_and_drains so rankings reflect
                    # the new data.
                    await run_hypes_and_drains(conn, force=True)
                else:
                    # Even when sentiments + attributions are up to date, the
                    # hypes_and_drains table might be empty (first run after
                    # the SQL migration). Populate it if empty.
                    n_hd = await conn.fetchval(
                        "SELECT COUNT(*) FROM analysis.industry_hypes_and_drains"
                    )
                    if not n_hd:
                        print("    -> hypes_and_drains table empty — "
                              "populating...", flush=True)
                        await run_hypes_and_drains(conn, force=True)
                    else:
                        print("    -> DB is up to date; nothing to do.",
                              flush=True)
                print_wall_time(t0)
                return

        # ---- Step 1: load (date, code, industry_id, close) ---------------
        # In incremental mode, only target_dates + each code's first-close
        # row are loaded (rebase anchor) — full history is NOT loaded
        # because rebase_closes only needs the first close per code. In
        # force mode, full history is loaded for a complete recompute.
        incremental = (target_dates is not None and len(target_dates) > 0)
        if incremental:
            sorted_dates = sorted(target_dates)
            print(f"\n[1/6] Loading (date, code, industry_id, close, pe, "
                  f"stock_num) — INCREMENTAL: {len(sorted_dates)} target "
                  f"dates + first-close row per code (rebase anchor)...",
                  flush=True)
            rows = await conn.fetch(
                LOAD_INDEX_DATA_SQL_INCREMENTAL, sorted_dates
            )
        else:
            print("\n[1/6] Loading (date, code, industry_id, close, pe, "
                  "stock_num) from index_basic_stats LEFT JOIN index_valuation "
                  "JOIN sec_classification (compositioned indices only), stock_num "
                  "via LATERAL sec_composition latest-snapshot (no date filter)...",
                  flush=True)
            rows = await conn.fetch(LOAD_INDEX_DATA_SQL_FULL)
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
        # Index for the join on stock_code (lookup into stock_liquidity_margin)
        await conn.execute(
            "CREATE INDEX _pu_stock ON _pu (stock_code)"
        )
        await conn.execute("ANALYZE _pu")
        n_pu = await conn.fetchval("SELECT COUNT(*) FROM _pu")
        print(f"    [5a] _pu temp table: {n_pu:,} rows "
              f"({time.time() - t_pool:.1f}s)", flush=True)

        # 5b. Final aggregation join — direct _pu x stock_liquidity_margin.
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
        if amt_df.empty:
            # No stock trading_amount data at all — skip the merge (an empty
            # amt_df would have float64 dtypes for date/industry_id/pool_size
            # because pandas defaults empty columns to float64, which then
            # clashes with result's object dtypes and aborts the merge).
            # Just attach a NULL total_trading_amount column to result.
            result["total_trading_amount"] = None
        else:
            # Normalize both `date` columns to object dtype with
            # datetime.date elements to match asyncpg's return type.
            result["date"] = pd.to_datetime(result["date"]).dt.date
            amt_df["date"] = pd.to_datetime(amt_df["date"]).dt.date
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

        # Sanitize the aggregated result for asyncpg upsert. Replaces the
        # per-row iterrows dict construction (with manual float()/int()
        # casts + NaN->None) with a single vectorized pass: round (skipped),
        # inf->NaN, NaN->None, to_dict. The non-numeric columns
        # (date, industry_id, pool_size, industry_label) pass through
        # unchanged.
        data = sanitize_for_db_insert(
            result,
            numeric_cols=[
                "index_count", "mean_price", "var_price",
                "mean_pe", "total_trading_amount",
            ],
        )

        # Pre-check: skip already-present dates (safety net — the
        # find_missing_analysis_dates pre-check in Step 0 already filters
        # target_dates, but this catches any edge cases). In force mode the
        # table was truncated so every row is new — skip the check.
        if not args.force:
            n_before = len(data)
            data = await filter_rows_to_missing_dates_async(conn, TABLE, data)
            n_skipped = n_before - len(data)
            if n_skipped > 0:
                print(f"    -> skip check: {n_skipped:,} of {n_before:,} rows "
                      f"already present (skipped)", flush=True)

        # Force mode: table is pre-truncated → use COPY (fastest path, no
        # ON CONFLICT overhead). Incremental mode: bulk_upsert_async with
        # ON CONFLICT (table is non-empty). Batch size raised to 5000 — the
        # previous 1000 was conservative; profiling on the 390K-row
        # sentiments table showed 5000 is ~2× faster with no memory pressure.
        if args.force:
            n = await copy_insert_async(conn, TABLE, data)
            print(f"    -> COPY-inserted {n:,} rows", flush=True)
        else:
            n = await bulk_upsert_async(
                conn, TABLE, data,
                key_columns=["date", "industry_id", "pool_size"],
                batch_size=5000,
            )
            print(f"    -> upserted {n:,} rows", flush=True)

        # ---- Register in analysis.analysis_identity -----------------------
        await upsert_analysis_identity(
            conn,
            name=ANALYSIS_NAME,
            detail_name=ANALYSIS_NAME,
            description=ANALYSIS_DESCRIPTION,
        )

        # ---- Step 7: INTERNAL correlations step --------------------------
        # Pairwise rolling Pearson correlation of industries' mean_price
        # series -> analysis.industry_correlations. Reuses this same
        # connection. See correlations.py for the full pipeline.
        # Passes target_dates so correlations are computed only for missing
        # dates (force flag cascades from the parent).
        await run_correlations(conn, target_dates=target_dates,
                               force=args.force)

        # ---- Step 8: INTERNAL sec_alloc_perf_attribution producer --------
        # Populate analysis.sec_alloc_perf_attribution (composition overlap
        # + ETF-market liquidity + rolling close correlations, Index x Index).
        # Reuses this same connection. This used to be a standalone
        # analyze.sec_alloc_perf_attribution package; it is now an internal
        # step because steps 9 + 10 below READ from this table. It manages
        # its OWN target_dates (its missing dates can differ from
        # industry_sentiments' missing dates). Force flag cascades from the
        # parent (force mode truncates + fully recomputes).
        await run_perf_attribution(conn, force=args.force)

        # ---- Step 9: INTERNAL attributions step -------------------------
        # Aggregate analysis.sec_alloc_perf_attribution shared_weight to the
        # industry level -> analysis.industry_attributions. Reuses this same
        # connection. See attributions.py for the full pipeline. Exits
        # gracefully if sec_alloc_perf_attribution is empty.
        await run_attributions(conn, target_dates=target_dates,
                               force=args.force)

        # ---- Step 10: INTERNAL etf_contribution step --------------------
        # Aggregate analysis.sec_alloc_perf_attribution.code_etf_trading_amount
        # to the industry level -> analysis.industry_etf_contribution. Reuses
        # this same connection. See etf_contribution.py for the full pipeline.
        # Exits gracefully if sec_alloc_perf_attribution has no index rows
        # with non-NULL code_etf_trading_amount.
        await run_etf_contribution(conn, target_dates=target_dates,
                                   force=args.force)

        # ---- Step 11: INTERNAL hypes_and_drains step --------------------
        # Pre-compute top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by
        # attribution contribution to composite broad-market benchmarks
        # (MAIN=SS+SZ, INNOV=GEM+STAR) -> analysis.industry_hypes_and_drains.
        # Reuses this same connection. See hypes_and_drains.py. Depends on
        # step 9 (attributions, incl. the 120d column) being populated first.
        # Always runs full recompute (truncate-then-recompute) — the table
        # is small (~245K rows max) and rankings shift when any date changes.
        await run_hypes_and_drains(conn, force=True)

        print_wall_time(t0)
    finally:
        # Close with a timeout — after heavy bulk inserts the PostgreSQL
        # server can be saturated with WAL checkpoint I/O, making
        # conn.close() stall on the Terminate message + TCP teardown.
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
