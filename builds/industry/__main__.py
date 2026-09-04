"""Entry point for builds.industry (stats.industry_basic_stats).

Run via ``python -m builds.industry``.

Migrated from analyze/industry_sentiments (2026-08-24): the per-industry
BASELINE aggregation is a stats-level table (mirroring builds.index /
builds.stock); the downstream analysis steps remain in
analyze.industry_sentiments and read from stats.industry_basic_stats.

Pipeline
  1. Load (date, code, industry_id, open, high, low, close, pe, stock_num)
     from index_basic_stats LEFT JOIN index_valuation JOIN sec_classification
     (compositioned indices only; synthetic DUMMY_* indices skipped when
     empty), stock_num via LATERAL sec_composition latest-snapshot (no date
     filter — temporal extrapolation). pe == 0 is treated as no-data (NULL)
     so the mean never averages a 0-marker in. In incremental mode, filters
     to target_dates PLUS one first-close row per code (rebase anchor); in
     force mode, loads full history.
  2. Rebase each code's OHLC to 100 at its first available close (single
     per-index scale factor applied to open/high/low/close — the composite
     index OHLC convention).
  3. Classify pool_size from stock_num (small/mid/large; NULL -> 'all').
  4. Aggregate mean_open/mean_high/mean_low/mean_close (former mean_price
     rehooked to mean_close) + var_price/mean_pe per (date, industry_id,
     pool).
  5. SEPARATE SQL: total_trading_amount = SUM(stock_liquidity_margin.trading_amount)
     across the UNION of stocks from member indices' compositions.
  6. Merge + upsert stats.industry_basic_stats.

Default (incremental) mode:
  Only dates present in stats.index_identity but NOT yet in
  stats.industry_basic_stats are (re)computed and upserted. Step 1 loads
  only target-date rows PLUS one first-close row per code (the rebase
  anchor) — full per-code history is NOT loaded, which saves ~99% of rows
  when target_dates is small.

--force mode:
  Truncates stats.industry_basic_stats first, then recomputes and inserts
  all rows.

--date YYYY-MM-DD mode:
  Recomputes and upserts ONLY that single date (it must exist in
  stats.index_identity), bypassing the missing-date skip — existing rows
  are refreshed through the upsert path (no truncation, no deletes).
  Mutually exclusive with --force.
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import argparse
import asyncio
import datetime
import os
import sys
import time
from typing import Optional, Set

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m builds.industry`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    copy_or_upsert_split_async,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    find_missing_analysis_dates,
    filter_rows_to_missing_dates_async,
    add_force_arg,
    add_date_arg,
    enforce_date_force_exclusion,
    parse_date_arg,
    forced_date_scope,
    rec_col,
    rec_cols,
)
from _common.db_commons import batched_copy_by_key_async  # noqa: E402

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import pandas as pd  # noqa: E402

from _common.df_utils import epoch_col_to_dt64, sanitize_for_db_insert, to_py_dates  # noqa: E402
from builds.industry.config import TABLE  # noqa: E402
from builds.industry.compute import (  # noqa: E402
    rebase_ohlc,
    aggregate_by_pool,
)


# ---------------------------------------------------------------------------
#  SQL queries (kept in __main__ — they are tightly coupled to the async
#  fetch flow and not reused elsewhere).
# ---------------------------------------------------------------------------

# Step 1 — load per-(date, code, industry_id, OHLC, pe, stock_num) rows.
# stock_num is looked up via LATERAL latest-snapshot per code (no date
# filter — temporal extrapolation: the current pool_size classification
# is used as a proxy for historical membership).
#
# DUMMY-INDEX FILTER: synthetic DUMMY_* indices (sec_classification.is_dummy
# = TRUE — placeholder parents for orphan ETFs) are skipped when empty,
# i.e. when they carry no index_basic_stats rows. They never do, so in
# practice all dummy indices are excluded; the guard keeps the intent
# explicit should a dummy ever gain data.
#
# Two variants share a common template:
#   * FULL          — every (date, code) row. Used in --force mode so the
#                     full rebase + aggregate runs over the complete history.
#   * INCREMENTAL   — only target_dates PLUS each code's FIRST available
#                     close (the rebase anchor). rebase_ohlc only needs
#                     each code's first close to compute the scale factor
#                     100 / first_close — intermediate dates are not
#                     needed. Saves ~99% of rows when target_dates is
#                     small. The first_close CTE filters close > 0 to
#                     match pandas' post-load filter
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
        extract(epoch from ib.date)::float8 AS date,
        ib.code,
        sc.industry_id,
        COALESCE(sc.industry_label, sc.industry_id) AS industry_label,
        ib.open,
        ib.high,
        ib.low,
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
      AND NOT (
          sc.is_dummy = TRUE
          AND NOT EXISTS (
              SELECT 1 FROM stats.index_basic_stats ibx
              WHERE ibx.code = sc.code
                AND ibx.close IS NOT NULL
          )
      )
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
_POOL_AMOUNT_JOIN_SQL_FULL = """
    SELECT
        extract(epoch from slm.date)::float8 AS date,
        pu.industry_id,
        pu.pool_size,
        SUM(slm.trading_amount) AS total_trading_amount
    FROM _pu pu
    JOIN stats.stock_liquidity_margin slm
        ON slm.code = pu.stock_code
    WHERE slm.trading_amount IS NOT NULL
    GROUP BY slm.date, pu.industry_id, pu.pool_size
"""

_POOL_AMOUNT_JOIN_SQL_INCREMENTAL = """
    SELECT
        extract(epoch from slm.date)::float8 AS date,
        pu.industry_id,
        pu.pool_size,
        SUM(slm.trading_amount) AS total_trading_amount
    FROM _pu pu
    JOIN stats.stock_liquidity_margin slm
        ON slm.code = pu.stock_code
    WHERE slm.trading_amount IS NOT NULL
      AND slm.date = ANY($1::date[])
    GROUP BY slm.date, pu.industry_id, pu.pool_size
"""


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Industry basic stats build (composite rebased-to-100 "
                    "OHLC mean/var per date x industry x pool_size)."
    )
    add_force_arg(ap)
    add_date_arg(ap)
    args = ap.parse_args()

    # --date / --force are mutually exclusive; parse the forced date early.
    enforce_date_force_exclusion(args)
    forced = parse_date_arg(args.date)

    t0 = time.time()
    print_build_header(
        "BUILD INDUSTRY BASIC STATS "
        "(composite rebased-to-100 OHLC per date x industry x pool_size)",
        index_table=TABLE,
        mode="FORCE (full recompute)" if args.force
             else f"DATE MODE (forced single date: {forced})" if forced is not None
             else "incremental (missing dates only)",
    )
    if forced is not None:
        print(f"[DATE MODE] Forced single-date build: {forced}", flush=True)

    conn = await get_db_connection_async()
    try:
        # ---- Step 0: determine target dates -------------------------------
        if forced is not None:
            # --date mode: bypass the DB missing-date skip — the forced date
            # is ALWAYS recomputed and upserted (existing rows refresh via
            # the upsert path below; no truncation, no deletes). The date
            # must exist in the source identity table, else there is no
            # source data to aggregate for it.
            print(f"    -> --date mode: checking stats.index_identity for "
                  f"{forced}...", flush=True)
            id_rows = await conn.fetch(
                "SELECT DISTINCT date FROM stats.index_identity WHERE date = $1",
                forced,
            )
            target_dates = forced_date_scope(
                {r["date"] for r in id_rows}, forced,
                source_label="stats.index_identity",
            )
        elif args.force:
            print("\n[0/6] Force mode: truncating industry_basic_stats...",
                  flush=True)
            await truncate_table_async(conn, TABLE)
            target_dates: Optional[Set[datetime.date]] = None
            print("    -> truncated; will recompute all rows", flush=True)
        else:
            print("\n[0/6] Detecting missing dates "
                  "(source: index_identity vs industry_basic_stats)...",
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

        # ---- Step 1: load (date, code, industry_id, OHLC, pe, stock_num) --
        # In incremental mode, only target_dates + each code's first-close
        # row are loaded (rebase anchor) — full history is NOT loaded
        # because rebase_ohlc only needs the first close per code. In
        # force mode, full history is loaded for a complete recompute.
        incremental = (target_dates is not None and len(target_dates) > 0)
        if incremental:
            sorted_dates = sorted(target_dates)
            print(f"\n[1/6] Loading (date, code, industry_id, OHLC, pe, "
                  f"stock_num) — INCREMENTAL: {len(sorted_dates)} target "
                  f"dates + first-close row per code (rebase anchor)...",
                  flush=True)
            rows = await conn.fetch(
                LOAD_INDEX_DATA_SQL_INCREMENTAL, sorted_dates
            )
        else:
            print("\n[1/6] Loading (date, code, industry_id, OHLC, pe, "
                  "stock_num) from index_basic_stats LEFT JOIN index_valuation "
                  "JOIN sec_classification (compositioned indices only), stock_num "
                  "via LATERAL sec_composition latest-snapshot (no date filter)...",
                  flush=True)
            rows = await conn.fetch(LOAD_INDEX_DATA_SQL_FULL)
        print(f"    -> {len(rows):,} rows across "
              f"{len(set(zip(rec_col(rows, 'industry_id'), rec_col(rows, 'code'))))} "
              f"(industry, code) pairs", flush=True)

        if not rows:
            print("    -> no data; aborting.", flush=True)
            return

        # Whole-column extraction + vectorized conversions (no per-row
        # float()/int() loops — NaN handling is delegated to the numeric
        # dtype; NULLs arrive as None → NaN in float columns automatically).
        df = pd.DataFrame(rec_cols(rows))
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["pe"] = df["pe"].astype(float)
        # PE == 0 is a "no data" marker, never a real valuation (impossible
        # for a price>0 index; SZSE-style loss-making marker). Treat as NULL
        # so aggregate_by_pool's mean SKIPS it — a slice with no usable PE
        # lands NULL (empty in the UI), never 0.
        df["pe"] = df["pe"].where(df["pe"] != 0)
        df["stock_num"] = df["stock_num"].astype("Int64")
        # datetime64[ns] date column — GPU-native through the whole
        # pipeline. The date column arrives as NATIVE float8
        # (extract(epoch) in SQL) and epoch_col_to_dt64 materializes the
        # explicit [ns] unit in ONE host pass. Object-dtype python dates
        # would raise cuDF MixedTypeError and poison the frame into CPU
        # fallbacks (see compute.py docstring). Converted back to python
        # dates only at the DB-insert boundary. [ns] pins the resolution
        # (cudf.pandas dtype would otherwise depend on backend — a
        # mixed-resolution merge breaks under cudf.pandas).
        df["date"] = epoch_col_to_dt64(df["date"], unit="ns", index=df.index)
        # Drop rows with NaN/zero close (can't rebase from zero).
        df = df[df["close"].notna() & (df["close"] > 0)].copy()

        # ---- Step 2: rebase each code's OHLC to 100 at its first close ----
        print("\n[2/6] Rebased-to-100 OHLC at per-index first available close "
              "(history start, single scale factor per index)...",
              flush=True)
        df = rebase_ohlc(df)
        print(f"    -> rebased {len(df):,} rows across {df['code'].nunique()} "
              f"indices", flush=True)

        # ---- Incremental filter: keep only target_dates for aggregation -----
        if target_dates is not None and len(target_dates) > 0:
            n_before = len(df)
            # datetime64 isin needs Timestamp targets (python dates would
            # never match a datetime64 column).
            ts_targets = pd.to_datetime(sorted(target_dates))
            df = df[df["date"].isin(ts_targets)].reset_index(drop=True)
            print(f"    -> incremental filter: {len(df):,} of {n_before:,} rows "
                  f"are in target_dates (rebase context rows dropped)",
                  flush=True)

        # ---- Step 3 + 4: classify pool_size + aggregate -------------------
        print("\n[3/6] Classifying pool_size from stock_num "
              "(small<51, mid 51-180, large >180; NULL->'all' only)...",
              flush=True)
        print("\n[4/6] Aggregating mean_open/high/low/close + var_price/mean_pe "
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

        # Whole-column extraction; NULLs arrive as None → NaN via float dtype
        amt_df = pd.DataFrame(rec_cols(amt_rows))
        amt_df["total_trading_amount"] = amt_df["total_trading_amount"].astype(float)
        # Match the main frame's datetime64[ns] date dtype for a
        # GPU-native merge (epoch over float8 -> explicit [ns] unit).
        amt_df["date"] = epoch_col_to_dt64(
            amt_df["date"], unit="ns", index=amt_df.index)

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
            result = result.merge(
                amt_df, on=["date", "industry_id", "pool_size"], how="left"
            )
            # Rows without stock amount data keep NaN — the numeric pass
            # of sanitize_for_db_insert below converts NaN/inf -> None
            # (asyncpg SQL NULL); a pre-boundary .where(notna, None) would
            # cast the column to object and poison subsequent cudf ops.
        # Partition-key-major layout: sort by industry_id (the table's
        # HASH partition key) so downstream sanitize + writes stream
        # whole-industry runs — matching the key-batched write pattern.
        # Sorts while date is still datetime64 (GPU-native sort); the
        # python-date conversion below must come AFTER it.
        result = result.sort_values(
            ["industry_id", "date", "pool_size"]
        ).reset_index(drop=True)
        n_with_amt = result["total_trading_amount"].notna().sum()
        print(f"    -> {len(result):,} total rows | "
              f"{n_with_amt:,} with total_trading_amount | "
              f"{len(result) - n_with_amt:,} without (NULL)", flush=True)

        # DB-insert boundary: asyncpg needs python datetime.date objects —
        # ONE host numpy pass (a cudf-backed .dt.date falls back per
        # element).
        to_py_dates(result, ["date"])

        # Sanitize the aggregated result for asyncpg upsert. Replaces the
        # per-row iterrows dict construction (with manual float()/int()
        # casts + NaN->None) with a single vectorized pass: round (skipped),
        # inf->NaN, NaN->None, to_dict. The non-numeric columns
        # (date, industry_id, pool_size, industry_label) pass through
        # unchanged.
        data = sanitize_for_db_insert(
            result,
            numeric_cols=[
                "index_count", "mean_open", "mean_high", "mean_low",
                "mean_close", "var_price", "mean_pe", "total_trading_amount",
            ],
        )

        # Pre-check: skip already-present dates (safety net — the
        # find_missing_analysis_dates pre-check in Step 0 already filters
        # target_dates, but this catches any edge cases). In force mode the
        # table was truncated so every row is new — skip the check.
        # --date mode bypasses it too: already-present forced-date rows must
        # be REFRESHED via the upsert below, not skipped.
        if not args.force and forced is None:
            n_before = len(data)
            data = await filter_rows_to_missing_dates_async(conn, TABLE, data)
            n_skipped = n_before - len(data)
            if n_skipped > 0:
                print(f"    -> skip check: {n_skipped:,} of {n_before:,} rows "
                      f"already present (skipped)", flush=True)

        # Force mode: table is pre-truncated → key-batched COPY (whole
        # industry_id groups per chunk — the table's HASH partition key;
        # never splits an industry across chunks). Incremental mode:
        # copy_or_upsert_split_async splits at MAX(date) so new dates use
        # COPY and gaps use upsert.
        if args.force:
            n = await batched_copy_by_key_async(
                conn, TABLE, data, key="industry_id", label="baseline",
            )
            print(f"    -> key-batched COPY-inserted {n:,} rows", flush=True)
        else:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, TABLE, data,
                key_columns=["date", "industry_id", "pool_size"],
            )
            n = n_copied + n_upserted
            via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                  f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                  "upsert"
            print(f"    -> inserted {n:,} rows via {via}", flush=True)

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
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
