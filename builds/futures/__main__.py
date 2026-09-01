"""builds/futures/__main__.py — Build CFFEX futures baseline data → database.

Reads per-day futures CSV files from temps/cffex_archive/YYYYMM/YYYYMMDD_futures.csv
and inserts into:
  - stats.futures_identity   (PK: date, code)
  - stats.futures_basic_stats (FK: date, code → identity)

Missing-data detection flow:
  1. Glob all *_futures.csv files under CFFEX archive
  2. Extract available dates from filenames
  3. Query stats.futures_identity for existing dates in DB
  4. missing_dates = available_dates - existing_dates
  5. Read ONLY source files whose date is in missing_dates
  6. Parse contract codes → build identity + basic_stats DataFrames
  7. Bulk upsert into stats.futures_identity + stats.futures_basic_stats

With --force: truncate both tables first, so all source dates are treated
as missing. With --force --code <contract>: only that contract's rows are
deleted instead of truncating.

With --date YYYY-MM-DD: build ONLY that single date and bypass the DB
missing-date skip — the date is (re)built even if already present (existing
rows are refreshed through the normal upsert path; no truncation, no
deletes). Mutually exclusive with --force.

Usage:
  python -m builds.futures
  python -m builds.futures --start-date 2024-01-01 --end-date 2026-07-23
  python -m builds.futures --date 2026-07-23           (force single-date rebuild)
  python -m builds.futures --force
  python -m builds.futures --code IF2609              (single-contract test filter)
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import os
import sys
import time
import argparse
from datetime import date as _date
from typing import List

import warnings
warnings.filterwarnings("ignore")

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import numpy as np
import pandas as pd

from _common.build_commons import (
    setup_utf8_stdout,
    add_common_build_args,
    enforce_date_force_exclusion,
    parse_date_arg,
    forced_date_scope,
    get_db_or_exit,
    find_missing_dates,
    truncate_table_async,
    copy_or_upsert_split_async,
    print_build_header,
    print_wall_time,
    TODAY_STR,
)
from builds._commons.code_filter import (
    add_code_arg,
    find_missing_dates_code_aware,
    normalize_code,
)
from builds._commons.row_emission import records_from_frame
from _common.df_utils import to_py_dates

setup_utf8_stdout()

import asyncio

from builds.futures.paths import CFFEX_ARCHIVE_DIR
from builds.futures.loader import (
    glob_futures_files,
    filter_files_by_dates,
    ymd_from_futures_filename,
    ymd_to_date,
    build_futures_df,
)


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build CFFEX futures baseline data and insert to database (missing dates only)."
    )
    add_common_build_args(ap)
    add_code_arg(ap)
    args = ap.parse_args()

    # --date / --force are mutually exclusive; parse the forced date early.
    enforce_date_force_exclusion(args)
    forced = parse_date_arg(args.date)
    if forced is not None:
        # Single-date scope overrides any explicit --start-date/--end-date.
        args.start_date = args.end_date = forced.isoformat()

    # CFFEX contract codes (e.g. IF2609) carry no exchange suffix — strip
    # whatever normalize_code may have appended to a bare code.
    code_filter = normalize_code(args.code)
    if code_filter:
        code_filter = code_filter.split(".")[0]

    t0 = time.time()
    print_build_header(
        "BUILD CFFEX FUTURES BASELINE  ·  missing-dates-only → DATABASE",
        **{
            "CFFEX archive dir": CFFEX_ARCHIVE_DIR,
            "Date range":       f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Code filter":      code_filter or "(none — all contracts)",
            "Today":            TODAY_STR,
        }
    )
    if code_filter:
        print(f"    [CODE FILTER] Restricting build to single contract: {code_filter}", flush=True)
    if forced is not None:
        print(f"[DATE MODE] Forced single-date build: {forced}", flush=True)

    # ------------------------------------------------------------------
    # 1. Discover source files and available dates
    # ------------------------------------------------------------------
    print("\n[1/3] Discovering source CSV files …", flush=True)
    all_files = glob_futures_files(CFFEX_ARCHIVE_DIR)
    print(f"    → {len(all_files)} *_futures.csv files available", flush=True)

    if not all_files:
        print("    [FATAL] No futures CSV files found", flush=True)
        sys.exit(1)

    # Extract available dates from filenames (stdlib date — no proxied
    # Timestamp .date() calls, one PER FILE under cudf.pandas)
    available_dates: set[_date] = set()
    for f in all_files:
        ymd = ymd_from_futures_filename(f)
        if ymd:
            d = ymd_to_date(ymd)
            if d is not None:
                available_dates.add(d)

    # Apply date range filter
    if args.start_date:
        start_d = pd.to_datetime(args.start_date).date()
        available_dates = {d for d in available_dates if d >= start_d}
    if args.end_date:
        end_d = pd.to_datetime(args.end_date).date()
        available_dates = {d for d in available_dates if d <= end_d}

    print(f"    → {len(available_dates)} unique dates available in range", flush=True)

    # ------------------------------------------------------------------
    # 2. Connect to DB and find missing dates
    # ------------------------------------------------------------------
    print("\n[2/3] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()

    try:
        if forced is not None:
            # --date mode: bypass the DB missing-date skip — the forced date
            # is ALWAYS processed (rows already in the DB are refreshed via
            # the upsert path below; no truncation, no deletes).
            missing_dates = forced_date_scope(available_dates, forced)
        elif args.force:
            if code_filter:
                # Single-code force mode: delete only this contract's rows
                # (FK child first, identity last) instead of truncating.
                print(f"    [DB] Force mode for code {code_filter}: deleting existing rows for this code", flush=True)
                await conn.execute(
                    "DELETE FROM stats.futures_basic_stats WHERE code = $1", code_filter
                )
                await conn.execute(
                    "DELETE FROM stats.futures_identity WHERE code = $1", code_filter
                )
            else:
                print("    [DB] Force mode: truncating existing tables", flush=True)
                await truncate_table_async(conn, "stats.futures_basic_stats")
                await truncate_table_async(conn, "stats.futures_identity")
            missing_dates = available_dates
        elif code_filter:
            # Single-code mode: only check this contract's dates so dates
            # loaded for OTHER contracts don't mask this code's gaps.
            missing_dates = await find_missing_dates_code_aware(
                conn, "stats.futures_identity", available_dates, code_filter
            )
        else:
            missing_dates = await find_missing_dates(
                conn, "stats.futures_identity", available_dates
            )

        print(
            f"    [DB] {len(missing_dates)} dates missing from "
            f"stats.futures_identity (out of {len(available_dates)} available)",
            flush=True,
        )

        if not missing_dates:
            print(
                "    [INFO] Database is up to date — no new futures dates to insert",
                flush=True,
            )
            print_wall_time(t0)
            return

        # ------------------------------------------------------------------
        # 3. Filter to missing-date files and build rows
        # ------------------------------------------------------------------
        print(
            f"\n[3/3] Reading source CSVs for {len(missing_dates)} missing dates …",
            flush=True,
        )
        missing_files = filter_files_by_dates(all_files, missing_dates)
        print(f"    → {len(missing_files)} source CSV files to read", flush=True)

        if not missing_files:
            print("    [INFO] No source files for missing dates", flush=True)
            print_wall_time(t0)
            return

        identity_df, basic_df = build_futures_df(missing_files, verbose=True)

        # Filter to the target contract if --code is set (boolean-mask
        # results are fresh frames; DB emission is index-blind — no reindex)
        if code_filter:
            if len(identity_df) > 0:
                n_before = len(identity_df)
                identity_df = identity_df[identity_df["code"] == code_filter]
                print(f"    [CODE FILTER] Identity rows {n_before:,} → {len(identity_df):,} for code {code_filter}", flush=True)
            if len(basic_df) > 0:
                n_before = len(basic_df)
                basic_df = basic_df[basic_df["code"] == code_filter]
                print(f"    [CODE FILTER] Basic-stats rows {n_before:,} → {len(basic_df):,} for code {code_filter}", flush=True)

        if identity_df.empty or basic_df.empty:
            print("    [INFO] No futures rows parsed from missing-date files", flush=True)
            print_wall_time(t0)
            return

        n_codes = identity_df["code"].nunique()
        d0 = identity_df["date"].min()
        d1 = identity_df["date"].max()
        print(
            f"    → {len(identity_df):,} identity rows · {n_codes} contracts",
            flush=True,
        )
        print(f"    → date range: {d0} → {d1}", flush=True)

        # ------------------------------------------------------------------
        # 4. Insert to database
        # ------------------------------------------------------------------
        print("\n[DB] Inserting data …", flush=True)

        # Convert dates to datetime.date for asyncpg — keep the columns
        # datetime64 until this boundary, then ONE host numpy pass per
        # column (a cudf-backed .dt.date falls back per element).
        identity_db = identity_df.copy()
        identity_db["date"] = pd.to_datetime(identity_db["date"])
        to_py_dates(identity_db, ["date"])

        basic_db = basic_df.copy()
        basic_db["date"] = pd.to_datetime(basic_db["date"])
        to_py_dates(basic_db, ["date"])

        # Dedupe within batch to avoid ON CONFLICT issues
        identity_db = identity_db.drop_duplicates(
            subset=["date", "code"], keep="last"
        )
        basic_db = basic_db.drop_duplicates(
            subset=["date", "code"], keep="last"
        )

        # Build row dicts for bulk upsert (zip-based assembly, no
        # to_dict("records") — cudf.pandas falls back once per row)
        identity_rows: List[dict] = records_from_frame(
            identity_db, np.asarray(identity_db.columns).tolist())
        basic_rows: List[dict] = records_from_frame(
            basic_db, np.asarray(basic_db.columns).tolist())

        pk_cols = ["date", "code"]

        # Insert identity first (FK parent)
        if identity_rows:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, "stats.futures_identity", identity_rows, pk_cols
            )
            total = n_copied + n_upserted
            via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                  f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                  "upsert"
            print(
                f"    [DB] Inserted {total:,} rows into stats.futures_identity via {via}",
                flush=True,
            )
        else:
            print(
                "    [DB] No new identity rows to insert into stats.futures_identity",
                flush=True,
            )

        # Insert basic_stats
        if basic_rows:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, "stats.futures_basic_stats", basic_rows, pk_cols
            )
            total = n_copied + n_upserted
            via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                  f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                  "upsert"
            print(
                f"    [DB] Inserted {total:,} rows into stats.futures_basic_stats via {via}",
                flush=True,
            )
        else:
            print(
                "    [DB] No new basic_stats rows to insert into stats.futures_basic_stats",
                flush=True,
            )

    finally:
        await conn.close()

    # Console summary
    if not identity_df.empty:
        print(f"\n  Product distribution:", flush=True)
        for product_code, sub in identity_df.groupby("product_code"):
            n_dates = int(sub["date"].nunique())
            n_contracts = int(sub["code"].nunique())
            print(
                f"    · {product_code:<4s} "
                f"{sub['name'].iloc[0]:<20s} "
                f"{n_dates:>4d} days  {n_contracts:>3d} contracts",
                flush=True,
            )

    print_wall_time(t0)


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()