"""builds/options/cffex/__main__.py — Build CFFEX options data and insert to database.

Reads per-day CFFEX options CSV files from:
  - temps/cffex_archive/YYYYMM/YYYYMMDD_options.csv (archive)
  - temps/cffex_options_trend/YYYYMM/YYYYMMDD_options.csv (trend/downloaded)
  - temps/cffex_trend/YYYYMM/YYYYMMDD_options.csv (futures trend, also has options)

Inserts into 7 options tables (same schema as SZSE options):
  - stats.options_identity   (PK: date, contract_code)
  - stats.options_terms      (FK: date, contract_code → identity)
  - stats.options_strike     (FK: date, contract_code → identity)
  - stats.options_settlement (FK: date, contract_code → identity)
  - stats.options_greeks     (FK: date, contract_code → identity)
  - stats.options_volume_oi  (FK: date, contract_code → identity)
  - stats.options_aggregate  (FK: date, contract_code → identity)

Missing-data detection flow:
  1. Glob all *_options.csv files under archive + trend + futures_trend directories
  2. Extract available dates from filenames
  3. Query SELECT DISTINCT date FROM stats.options_identity → existing dates
  4. missing_dates = available_dates - existing_dates
  5. Read ONLY option files whose date is in missing_dates
  6. Parse contracts, compute derived columns (moneyness, ratios, IV, Greeks)
  7. Bulk upsert into 7 options_* tables

With --force: truncate all 7 options_* tables first, so all source dates
are treated as missing.

With --date YYYY-MM-DD: scope the run to that single date and bypass the DB
missing-date skip — the date is always (re)processed and rows already in the
DB are refreshed through the ON CONFLICT upsert write path (no truncation,
no deletes). Mutually exclusive with --force.

Usage:
  python -m builds.options.cffex
  python -m builds.options.cffex --start-date 2026-07-01 --end-date 2026-07-31
  python -m builds.options.cffex --force
  python -m builds.options.cffex --code 000300           (single-underlying test filter)
  python -m builds.options.cffex --date 2026-08-28       (force single-date rebuild, no DB skip)
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import argparse
import asyncio
import os
import sys
import time
from datetime import date, datetime
from typing import List, Optional

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import pandas as pd

import warnings
warnings.filterwarnings("ignore")

from _common.build_commons import (
    setup_utf8_stdout,
    add_common_build_args,
    get_db_or_exit,
    find_missing_dates,
    truncate_table_async,
    print_build_header,
    print_wall_time,
    rec_cols,
    TODAY_STR,
    enforce_date_force_exclusion,
    parse_date_arg,
    forced_date_scope,
    bulk_upsert_async,
)
from _common.df_utils import epoch_col_to_dt64
from builds._commons.code_filter import add_code_arg, normalize_code

setup_utf8_stdout()

from builds.options.cffex.config import (
    PRODUCT_CODES,
    PRODUCT_NAMES,
    PRODUCT_UNDERLYING,
    PRODUCT_TYPES,
)
from builds.options.cffex.paths import (
    CFFEX_ARCHIVE_DIR,
    CFFEX_OPTIONS_TREND_DIR,
    CFFEX_FUTURES_TREND_DIR,
    glob_options_files,
    ymd_from_options_filename,
)
from builds.options.cffex.loader import (
    build_options_df,
    filter_files_by_dates,
    ymd_to_date,
)

# Underlying index codes for CFFEX options (same as futures mapping)
# IO→000300, HO→000016, MO→000852, CO→000905
_INDEX_UNDERLYING_CODES = [code for code, _ in PRODUCT_UNDERLYING.values()]


async def load_index_ohlcv(
    conn,
    min_date: date,
    max_date: date,
) -> Optional[pd.DataFrame]:
    """Load index close prices from stats.index_basic_stats for moneyness calc.

    Args:
        conn: async database connection
        min_date: earliest date to fetch
        max_date: latest date to fetch

    Returns:
        DataFrame with columns: date, underlying_code, close
    """
    if not _INDEX_UNDERLYING_CODES:
        return None

    try:
        rows = await conn.fetch(
            """
            SELECT extract(epoch from date)::float8 AS date, code, close::float8 AS close
            FROM stats.index_basic_stats
            WHERE code = ANY($1)
              AND date >= $2
              AND date <= $3
            ORDER BY date, code
            """,
            _INDEX_UNDERLYING_CODES,
            min_date,
            max_date,
        )

        if not rows:
            return None

        # Whole-column extraction; vectorized conversions — the date
        # column arrives as float8 epoch (extract(epoch) in SQL) and is
        # materialized as datetime64[us] in ONE host pass.
        df = pd.DataFrame(rec_cols(rows))
        df = df.rename(columns={"code": "underlying_code"})
        df["underlying_code"] = df["underlying_code"].astype(str)
        df["close"] = df["close"].astype(float)
        df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
        return df

    except Exception:
        return None


# CFFEX option contract code prefixes to distinguish from SZSE contracts
_CFFEX_PREFIXES = ["IO%", "HO%", "MO%", "CO%"]


async def find_missing_cffex_dates(
    conn,
    source_dates: set[date],
    code_filter: str | None = None,
) -> set[date]:
    """Find dates from source_dates that do NOT already have CFFEX options data.

    Unlike find_missing_dates (which checks for ANY data in the table),
    this function only checks for rows whose contract_code starts with
    a CFFEX option product prefix (IO, HO, MO, CO). This prevents SZSE
    options data from masking dates that still need CFFEX data.

    With code_filter (bare underlying index code), the check is scoped to
    that underlying via stats.options_terms — dates loaded for OTHER
    underlyings no longer mask this underlying's gaps.
    """
    if not source_dates:
        return set()

    n = len(_CFFEX_PREFIXES)
    conditions = " OR ".join(
        [f'contract_code LIKE ${i+1}' for i in range(n)]
    )
    if code_filter:
        sql = (
            f'SELECT DISTINCT date FROM stats.options_terms '
            f'WHERE underlying_code = ${n+1} AND ({conditions})'
        )
        existing_rows = await conn.fetch(sql, *_CFFEX_PREFIXES, code_filter)
    else:
        sql = f'SELECT DISTINCT date FROM stats.options_identity WHERE {conditions}'
        existing_rows = await conn.fetch(sql, *_CFFEX_PREFIXES)
    existing_dates = {r["date"] for r in existing_rows if r["date"] is not None}

    return source_dates - existing_dates


# ============================================================================
# --date mode writer
# ============================================================================
async def upsert_split_tables_date_mode(conn, tables) -> None:
    """Upsert each split table (FK parent first) for --date mode.

    insert_split_tables' plain COPY is conflict-free only because rows are
    PK-checked missing dates upstream; --date mode intentionally re-processes
    rows that may already exist (refresh semantics), so every table is
    written with a bulk ON CONFLICT (date, contract_code) DO UPDATE upsert
    instead — no truncation, no deletes.
    """
    for tbl, rows in tables.items():
        if not rows:
            print(f"    [DB] No rows to upsert into {tbl}", flush=True)
            continue
        n = await bulk_upsert_async(conn, tbl, rows, key_columns=["date", "contract_code"])
        print(f"    [DB] Upserted {n:,} rows into {tbl}", flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build CFFEX options data and insert to database (missing dates only)."
    )
    add_common_build_args(ap)
    add_code_arg(ap)
    args = ap.parse_args()

    # --date / --force are mutually exclusive; parse the forced single date.
    # When set, the date also supersedes any explicit --start/--end range so
    # discovery/loading is scoped to this single day.
    enforce_date_force_exclusion(args)
    forced_date = parse_date_arg(args.date)
    if forced_date:
        args.start_date = forced_date.isoformat()
        args.end_date = forced_date.isoformat()

    # CFFEX option underlyings are bare 6-digit index codes (e.g. 000300) —
    # strip the exchange suffix normalize_code may have appended.
    code_filter = normalize_code(args.code)
    if code_filter:
        code_filter = code_filter.split(".")[0]

    t0 = time.time()
    print_build_header(
        "BUILD CFFEX OPTIONS  ·  missing-data-only → DATABASE",
        **{
            "Archive dir":       CFFEX_ARCHIVE_DIR,
            "Options trend dir": CFFEX_OPTIONS_TREND_DIR,
            "Futures trend dir": CFFEX_FUTURES_TREND_DIR,
            "Date range":        f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Code filter":       code_filter or "(none — all underlyings)",
            "Today":             TODAY_STR,
        },
    )
    if code_filter:
        print(f"    [CODE FILTER] Restricting build to single underlying: {code_filter}", flush=True)
        if code_filter not in _INDEX_UNDERLYING_CODES:
            print("    [CODE FILTER] Not a CFFEX index underlying — nothing to do for CFFEX; skipping", flush=True)
            print_wall_time(t0)
            return
    if forced_date:
        print(f"[DATE MODE] Forced single-date build: {forced_date}", flush=True)

    # ------------------------------------------------------------------
    # 1. Discover source files and available dates
    # ------------------------------------------------------------------
    print("\n[1/4] Discovering source CSV files …", flush=True)
    all_files = glob_options_files()
    print(f"    → {len(all_files)} *_options.csv files found (archive + options_trend + futures_trend)", flush=True)

    if not all_files:
        print("    [FATAL] No options CSV files found", flush=True)
        sys.exit(1)

    # Extract available dates from filenames
    available_dates: set[date] = set()
    for f in all_files:
        ymd = ymd_from_options_filename(f)
        if ymd:
            d = ymd_to_date(ymd)
            if d is not None:
                available_dates.add(d)

    # Apply date range filter
    if args.start_date:
        start_d = date.fromisoformat(args.start_date)
        available_dates = {d for d in available_dates if d >= start_d}
    if args.end_date:
        end_d = date.fromisoformat(args.end_date)
        available_dates = {d for d in available_dates if d <= end_d}

    print(f"    → {len(available_dates)} unique dates available in range", flush=True)

    # ------------------------------------------------------------------
    # 2. Connect to DB and find missing dates (CFFEX-only)
    # ------------------------------------------------------------------
    print("\n[2/4] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            if code_filter:
                # Single-code force mode: delete only this underlying's rows
                # instead of truncating (FK-safe order handled by the helper).
                print(f"    [DB] Force mode for underlying {code_filter}: deleting existing rows", flush=True)
                from builds.options.tables import delete_underlying_rows_async
                await delete_underlying_rows_async(conn, code_filter)
            else:
                print("    [DB] Force mode: truncating existing tables", flush=True)
                for tbl in (
                    "stats.options_aggregate",
                    "stats.options_volume_oi",
                    "stats.options_greeks",
                    "stats.options_settlement",
                    "stats.options_strike",
                    "stats.options_terms",
                    "stats.options_identity",
                ):
                    await truncate_table_async(conn, tbl)
            missing_dates = available_dates
        elif forced_date:
            # --date mode: bypass the DB missing-date skip — the forced date
            # is ALWAYS (re)processed; rows already in the DB are refreshed
            # through the ON CONFLICT upsert path in step 4 (no deletes).
            missing_dates = forced_date_scope(available_dates, forced_date)
        else:
            # With --code, only that underlying's dates are checked (via
            # options_terms) so other underlyings' loaded dates don't mask
            # this underlying's gaps.
            missing_dates = await find_missing_cffex_dates(conn, available_dates, code_filter=code_filter)

        print(
            f"    [DB] {len(missing_dates)} dates missing from "
            f"stats.options_identity (out of {len(available_dates)} available)",
            flush=True,
        )

        if not missing_dates:
            print(
                "    [INFO] Database is up to date — no new dates to insert",
                flush=True,
            )
            print_wall_time(t0)
            return

        # ------------------------------------------------------------------
        # 3. Read only missing-date source files and build options frame
        # ------------------------------------------------------------------
        print(f"\n[3/4] Reading source CSVs for {len(missing_dates)} missing dates …", flush=True)
        missing_files = filter_files_by_dates(all_files, missing_dates)
        print(f"    → {len(missing_files)} source CSV files to read", flush=True)

        if not missing_files:
            print("    [INFO] No source files for missing dates", flush=True)
            print_wall_time(t0)
            return

        # Load index close prices for moneyness calculation
        missing_min = min(missing_dates)
        missing_max = max(missing_dates)
        index_ohlcv = await load_index_ohlcv(conn, missing_min, missing_max)
        if index_ohlcv is not None and len(index_ohlcv) > 0:
            print(f"    [INDEX] Loaded {len(index_ohlcv)} index rows for moneyness", flush=True)
        else:
            print("    [INDEX] No index data available — moneyness will be 0", flush=True)

        options_df = build_options_df(missing_files, index_ohlcv)

        # Filter to the target underlying if --code is set — BEFORE the
        # derived columns, whose per-underlying aggregates (volume_pct,
        # total_volume_underlying, …) are computed within one underlying.
        if code_filter and len(options_df) > 0:
            n_before = len(options_df)
            options_df = options_df[
                options_df["underlying_code"] == code_filter
            ].reset_index(drop=True)
            print(f"    [CODE FILTER] Options rows {n_before:,} → {len(options_df):,} for underlying {code_filter}", flush=True)

        if len(options_df) == 0:
            print("    [INFO] No options rows parsed from missing-date files", flush=True)
            print_wall_time(t0)
            return

        print(f"    → {len(options_df):,} options rows  ·  {options_df['underlying_code'].nunique()} underlyings", flush=True)
        # Host boundary: a host-backed proxy frame still dispatches ops with
        # cudf implementations through the GPU path, so the frame must keep
        # only GPU-convertible dtypes (datetime64 dates, no object columns).
        # Python dates are emitted at the very last extraction step by
        # records_from_frame's numpy M-branch.
        if hasattr(options_df, "to_pandas"):  # GPU frame → host at DB boundary
            options_df = options_df.to_pandas()

        print(f"    → date range: {options_df['date'].min().date()} → {options_df['date'].max().date()}", flush=True)

        # ------------------------------------------------------------------
        # 4. Insert to database
        # ------------------------------------------------------------------
        print("\n[4/4] Inserting data to database …", flush=True)

        # Dates stay datetime64 on the frame: a .dt.date object column
        # poisons every later cudf op with MixedTypeError fallbacks. The
        # datetime64 columns are emitted as datetime.date by
        # records_from_frame's numpy M-branch (asyncpg DATE codec).
        options_db = options_df.copy()

        # Dedupe within batch
        options_db = options_db.drop_duplicates(subset=["date", "contract_code"], keep="last")

        # Split into the 7 options_* tables: plain COPY-insert when rows
        # are PK-checked missing dates (conflict-free), ON CONFLICT upsert
        # in --date mode (rows may already exist → refreshed, not duplicated).
        from builds.options.tables import build_split_tables, insert_split_tables

        tables = build_split_tables(
            options_db, underlying_target_type="INDEX", exchange="CFFEX",
        )
        if forced_date:
            # --date refresh: rows may already exist → ON CONFLICT upsert
            # (plain COPY in insert_split_tables would hit PK conflicts)
            await upsert_split_tables_date_mode(conn, tables)
        else:
            await insert_split_tables(conn, tables)

    finally:
        await conn.close()

    # Console summary
    if not options_df.empty:
        print(f"\n  Underlying distribution:", flush=True)
        for code, sub in options_df.groupby("underlying_code"):
            name = str(sub["underlying_name"].dropna().iloc[0]) if sub["underlying_name"].notna().any() else ""
            n_dates = int(sub["date"].dt.strftime("%Y-%m-%d").nunique())
            n_contracts = int(sub["contract_code"].nunique())
            n_strikes = int(sub["strike_price"].nunique())
            print(
                f"    · {code:<8s} {name:<16s} {n_dates:>4d} days  "
                f"{n_contracts:>4d} contracts  {n_strikes:>3d} strikes",
                flush=True,
            )

    print_wall_time(t0)


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()