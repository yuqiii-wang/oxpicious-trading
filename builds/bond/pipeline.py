"""builds.bond.pipeline — Build debt-market baseline and insert to database.

Aggregates daily-frequency data sources into the 8 debt_* tables:

  1. PBoC OMO (Open Market Operations) daily reverse-repo announcements
     → debt_omo table
  2. PBoC outright-repo tender announcements as date markers
     → debt_outright_repo table
  3. PBoC MLF (Medium-term Lending Facility) tender → debt_mlf table
  4. Repo lifecycle tracking (running cumulative)      → debt_repo table
  5. SHIBOR daily fixing rates                         → debt_shibor table
  6. China bond (中债国债) daily yield-curve data       → debt_treasury table
  7. PBoC LPR (Loan Prime Rate) monthly announcements   → debt_lpr table
  8. PBoC Open Market Announcements policy notices      → pboc_oma table

Missing-data detection flow (DB-first):
  1. Glob source files (filenames only — no reading)
  2. Read instruments CSV + LPR CSV (single files, fast) to discover
     available dates
  3. Query stats.debt_identity by index for existing dates
  4. missing_dates = available_dates - existing_dates
  5. If no missing dates: exit early (DB is up to date)
  6. Read OMO (full history for repo cumulative) + outright + MLF from
     instruments CSV; read LPR from lpr_combined.csv
  7. Filter SHIBOR/China bond yearly files to only those overlapping with
     missing dates' years, then read them
  8. After reading, check for additional dates from SHIBOR/China bond not
     in the instruments CSV
  9. Filter all frames to missing dates and bulk upsert into the 8 debt_* tables

With --force: truncate all 8 debt_* tables first, so all source dates are
treated as missing.

With --date YYYY-MM-DD: build ONLY that single date and bypass the DB
missing-date skip — the date is (re)built even if already present (existing
rows are refreshed through the normal upsert path; no truncation, no
deletes). The PBoC OMA table keeps its full truncate+reload (scoping it to
one date would wipe the other announcements). Mutually exclusive with
--force.

Usage:
  python -m builds.bond
  python -m builds.bond --start-date 2024-01-01 --end-date 2026-07-14
  python -m builds.bond --date 2026-07-14
  python -m builds.bond --force

Prerequisite:
  Run `python download_pboc_repo_news.py --reparse` first to (re)generate
  temp_data/analysis_output/pboc_repo_news/instruments_combined.csv.
  Run `python download_pboc_lpr_news.py` first to (re)generate
  temps/pboc_lpr_news/lpr_combined.csv.
  Run `python download_pboc_oma.py` first to (re)generate
  temps/pboc_oma_news/oma_combined.csv.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import time
from datetime import datetime

import numpy as np
import pandas as pd

from builds._commons.row_emission import dates_as_date_list, records_from_frame


def _host(df):
    """Unwrap a cudf-backed frame to host pandas ONCE before numpy
    extraction (dates_as_date_list / records_from_frame). Frames stay
    cudf-dispatched through all processing — host conversion happens
    only at this final boundary."""
    return df.to_pandas() if hasattr(df, "to_pandas") else df

from _common.build_commons import (
    add_common_build_args,
    enforce_date_force_exclusion,
    parse_date_arg,
    forced_date_scope,
    copy_or_upsert_split_async,
    get_db_or_exit,
    glob_source_files,
    print_build_header,
    print_wall_time,
    truncate_table_async,
    TODAY_STR,
)

from builds.bond.chinabond import (
    assert_chinabond_converted,
    build_chinabond_df,
    read_chinabond_csv,
)
from builds.bond.instruments import load_pboc_instruments_df
from builds.bond.pboc_lpr import build_lpr_df
from builds.bond.pboc_oma import build_oma_df
from builds.bond.pboc_omo import (
    build_pboc_mlf_df,
    build_pboc_omo_df,
    build_pboc_outright_repo_df,
)
from builds.bond.paths import (
    CHINABOND_DIR,
    PBOC_INSTRUMENTS_CSV,
    PBOC_LPR_CSV,
    PBOC_OMA_CSV,
    SHIBOR_DIR,
)
from builds.bond.repo_lifecycle import build_repo_lifecycle_df
from builds.bond.shibor import (
    assert_shibor_converted,
    build_shibor_df,
    read_shibor_csv,
)


# ============================================================================
# Source-file discovery helpers
# ============================================================================
def filter_files_by_missing_years(files, missing_dates):
    """Filter yearly files to those overlapping with missing dates' years.

    SHIBOR files: shibor_his_YYYY0101_YYYY1231.csv
    China bond files: chinabond_bzqx_treasury_bond_YYYY.csv

    Extracts 4-digit year tokens from filenames and keeps files whose years
    overlap with the set of years in missing_dates.
    """
    if not missing_dates:
        return []
    missing_years = {d.year for d in missing_dates}
    out = []
    for f in files:
        basename = os.path.basename(f)
        years_in_name = set(int(y) for y in re.findall(r'\d{4}', basename))
        if years_in_name & missing_years:
            out.append(f)
    return out


def latest_file_by_date_token(files, token_index=1):
    """Find the file whose filename contains the max YYYYMMDD date token.

    SHIBOR files are named shibor_his_YYYYMMDD_YYYYMMDD.csv; the second
    date token is the (inclusive) end date. Returns the file with the
    latest end date, or None if no file has a parseable token.
    """
    best = None
    best_end = None
    for f in files:
        tokens = re.findall(r'(\d{8})', os.path.basename(f))
        if len(tokens) <= token_index:
            continue
        try:
            end_dt = datetime.strptime(tokens[token_index], "%Y%m%d")
        except ValueError:
            continue
        if best_end is None or end_dt > best_end:
            best_end = end_dt
            best = f
    return best


def latest_file_by_year(files):
    """Find the file whose filename contains the max 4-digit year token.

    China bond files are named chinabond_bzqx_treasury_bond_YYYY.csv.
    """
    best = None
    best_year = -1
    for f in files:
        for y in re.findall(r'(\d{4})', os.path.basename(f)):
            yi = int(y)
            if 1990 <= yi <= 2100 and yi > best_year:
                best_year = yi
                best = f
    return best


def discover_dates_from_latest_files(shibor_files, chinabond_files, verbose=False):
    """Read the latest SHIBOR + China bond files to discover available dates.

    Returns a set of datetime.date. This augments the available-dates set
    BEFORE the missing-dates check so the early-exit does not fire when
    SHIBOR/China bond source files have newer dates than the instruments/LPR
    CSVs (which are the only sources the check otherwise considers).
    """
    dates = set()
    shibor_latest = latest_file_by_date_token(shibor_files)
    if shibor_latest:
        df = read_shibor_csv(shibor_latest)
        if df is not None and len(df) > 0:
            # ONE numpy pass on the host frame — Series .dt.date on a
            # cudf-backed frame is a slow path per element
            d_list = dates_as_date_list(_host(df)["日期"])
            dates |= set(d_list)
            if verbose and d_list:
                print(f"    [SHIBOR] latest file {os.path.basename(shibor_latest)}: "
                      f"max date={max(d_list)}", flush=True)
    chinabond_latest = latest_file_by_year(chinabond_files)
    if chinabond_latest:
        df = read_chinabond_csv(chinabond_latest)
        if df is not None and len(df) > 0:
            d_list = dates_as_date_list(_host(df)["日期"])
            dates |= set(d_list)
            if verbose and d_list:
                print(f"    [CHINABOND] latest file {os.path.basename(chinabond_latest)}: "
                      f"max date={max(d_list)}", flush=True)
    return dates


# ============================================================================
# Insert helper
# ============================================================================
async def insert_rows(conn, table: str, rows, pk_cols: list, suffix: str = "") -> None:
    """COPY/upsert rows into table and print the outcome line."""
    n_copied, n_upserted = await copy_or_upsert_split_async(conn, table, rows, pk_cols)
    total = n_copied + n_upserted
    via = "COPY" if n_copied > 0 and n_upserted == 0 else \
          f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
          "upsert"
    print(f"    [DB] Inserted {total:,} rows into {table} via {via}{suffix}", flush=True)


# ============================================================================
# Main pipeline
# ============================================================================
async def main():
    ap = argparse.ArgumentParser()
    add_common_build_args(ap)
    args = ap.parse_args()

    # --date / --force are mutually exclusive; parse the forced date early.
    enforce_date_force_exclusion(args)
    forced = parse_date_arg(args.date)
    # Single-date scope: every date-driven reader below is restricted to the
    # forced date. PBoC OMA (step 2b) is exempt — it always truncates+
    # reloads the whole table, so scoping it to one date would wipe all
    # other OMA rows.
    oma_start, oma_end = args.start_date, args.end_date
    if forced is not None:
        # Single-date scope overrides any explicit --start-date/--end-date.
        args.start_date = args.end_date = forced.isoformat()

    t0 = time.time()
    print_build_header(
        "BUILD DEBT MARKET BASELINE  ·  missing-dates-only → DATABASE",
        **{
            "PBoC instr CSV": PBOC_INSTRUMENTS_CSV,
            "PBoC LPR CSV":   PBOC_LPR_CSV,
            "PBoC OMA CSV":   PBOC_OMA_CSV,
            "SHIBOR dir":     SHIBOR_DIR,
            "China bond dir": CHINABOND_DIR,
            "Date range":     f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Today":          TODAY_STR,
        }
    )
    if forced is not None:
        print(f"[DATE MODE] Forced single-date build: {forced}", flush=True)

    if not os.path.exists(PBOC_INSTRUMENTS_CSV):
        # Non-fatal: OMA reload can still proceed. The debt_* tables just
        # won't get new dates from the instruments CSV.
        print(f"\n  [WARN] {PBOC_INSTRUMENTS_CSV} not found — debt_* tables will "
              f"not be updated. Run `python download_pboc_repo_news.py --reparse` "
              f"to enable debt loading. Continuing with OMA-only reload.", flush=True)

    # ------------------------------------------------------------------
    # (1) Discover source files (fast — filenames only, no reading)
    # ------------------------------------------------------------------
    print("\n[1/5] Discovering source files …", flush=True)
    shibor_files_all = glob_source_files(SHIBOR_DIR, "shibor_his_*.csv")
    chinabond_files_all = glob_source_files(CHINABOND_DIR, "chinabond_bzqx_treasury_bond_*.csv")
    # CSV ONLY — an xlsx whose canonical CSV was never converted is a
    # downloads bug: fail loudly here instead of silently skipping dates.
    assert_shibor_converted()
    assert_chinabond_converted()
    print(f"    → SHIBOR: {len(shibor_files_all)} yearly files", flush=True)
    print(f"    → China bond: {len(chinabond_files_all)} yearly files", flush=True)

    # ------------------------------------------------------------------
    # (2) Connect to DB and find missing dates
    # ------------------------------------------------------------------
    print("\n[2/5] Connecting to database and detecting missing dates …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: truncating existing tables", flush=True)
            for tbl in ("stats.pboc_oma",
                        "stats.debt_lpr", "stats.debt_treasury", "stats.debt_shibor",
                        "stats.debt_mlf", "stats.debt_outright_repo", "stats.debt_repo",
                        "stats.debt_omo", "stats.debt_identity"):
                await truncate_table_async(conn, tbl)

        # ------------------------------------------------------------------
        # (2b) Always reload PBoC OMA (small dataset, no FK to debt_identity)
        # ------------------------------------------------------------------
        print("\n[2b/5] Reloading PBoC OMA announcements (always truncate+insert) …", flush=True)
        # --date mode keeps the CLI range here (full reload by default) so
        # the truncate below never shrinks the table to a single date.
        oma_df = build_oma_df(oma_start, oma_end, verbose=True)
        # Always truncate so the table matches the latest CSV exactly. The
        # dataset is small (~15 rows) and announcements may occur on non-
        # trading days, so the missing-dates-only logic does not apply.
        await truncate_table_async(conn, "stats.pboc_oma")
        if oma_df is not None and len(oma_df) > 0:
            # Host unwrap + records_from_frame: datetime64 dates emit as
            # datetime.date (M-branch), NaN→None swept — no object-date
            # column, no to_dict("records") per-row proxy extraction
            oma_rows = _host(oma_df)
            oma_cols = np.asarray(oma_rows.columns).tolist()
            await insert_rows(conn, "stats.pboc_oma",
                              records_from_frame(oma_rows, oma_cols),
                              ["date", "title"])
        else:
            print(f"    [DB] No OMA rows to insert into stats.pboc_oma", flush=True)

        # ------------------------------------------------------------------
        # Discover available dates & find missing dates for debt_* tables
        # ------------------------------------------------------------------
        # Discover available dates from the instruments CSV + LPR CSV (fast)
        inst_df = load_pboc_instruments_df(verbose=True)
        all_available_dates = set()
        if inst_df is not None and len(inst_df) > 0:
            all_available_dates.update(dates_as_date_list(_host(inst_df)["pub_date"]))

        # LPR announcements are monthly — their dates must also be present
        # in debt_identity for the FK to hold.
        lpr_dates_only_df = build_lpr_df(verbose=False)
        if lpr_dates_only_df is not None and len(lpr_dates_only_df) > 0:
            all_available_dates.update(
                dates_as_date_list(_host(lpr_dates_only_df)["date"]))

        # Augment available dates with the latest SHIBOR + China bond file
        # dates. Without this, the early-exit below would fire whenever the
        # instruments/LPR CSVs are caught up — even if SHIBOR/China bond
        # source files have newer dates — and the freshest debt data would
        # never be loaded (the extra-dates merge at step 5 is unreachable
        # once the early-exit fires).
        file_dates = discover_dates_from_latest_files(
            shibor_files_all, chinabond_files_all, verbose=True)
        if file_dates:
            all_available_dates |= file_dates

        if forced is not None:
            # --date mode: bypass the DB missing-date skip — the forced date
            # is ALWAYS processed (existing rows refresh via the upsert path
            # below; no truncation, no deletes). The default discovery only
            # reads the LATEST SHIBOR/China bond yearly file, so augment the
            # discovered dates with the forced year's files first — otherwise
            # a historical date would be reported as having no source data.
            all_available_dates |= discover_dates_from_latest_files(
                filter_files_by_missing_years(shibor_files_all, {forced}),
                filter_files_by_missing_years(chinabond_files_all, {forced}),
            )
            missing_dates = forced_date_scope(all_available_dates, forced)
            existing_dates_set = set()
        elif args.force:
            existing_dates_set = set()
            missing_dates = all_available_dates
        else:
            existing_rows = await conn.fetch("SELECT DISTINCT date FROM stats.debt_identity")
            existing_dates_set = {r["date"] for r in existing_rows}
            missing_dates = all_available_dates - existing_dates_set
        print(f"    [DB] {len(missing_dates)} dates missing from stats.debt_identity", flush=True)

        if not missing_dates:
            print("    [INFO] Database is up to date — no new debt dates to insert "
                  "(OMA already reloaded above)", flush=True)
            print_wall_time(t0)
            return

        # ------------------------------------------------------------------
        # (3) Read OMO (full history for repo cumulative) + build outright/MLF/LPR
        # ------------------------------------------------------------------
        print("\n[3/5] Building PBoC OMO + outright + MLF + LPR (full history for repo cumulative) …", flush=True)
        # NOTE: OMO is read WITHOUT date filtering so the repo lifecycle
        # cumulative balance is computed over the full history. Only the
        # INSERT step filters to missing dates.
        omo_df = build_pboc_omo_df(verbose=True)
        outright_df = build_pboc_outright_repo_df(args.start_date, args.end_date, verbose=True)
        mlf_df = build_pboc_mlf_df(args.start_date, args.end_date, verbose=True)
        lpr_df = build_lpr_df(args.start_date, args.end_date, verbose=True)

        # ------------------------------------------------------------------
        # (4) Read SHIBOR + China bond (filtered to missing years) + repo lifecycle
        # ------------------------------------------------------------------
        print("\n[4/5] Building SHIBOR + repo lifecycle + China bond (missing years only) …", flush=True)

        # Filter yearly files to only those overlapping with missing dates
        missing_shibor_files = filter_files_by_missing_years(shibor_files_all, missing_dates)
        missing_chinabond_files = filter_files_by_missing_years(chinabond_files_all, missing_dates)
        print(f"    → SHIBOR: {len(missing_shibor_files)} files to read "
              f"(out of {len(shibor_files_all)} total)", flush=True)
        print(f"    → China bond: {len(missing_chinabond_files)} files to read "
              f"(out of {len(chinabond_files_all)} total)", flush=True)

        shibor_df = build_shibor_df(args.start_date, args.end_date, verbose=True,
                                     files=missing_shibor_files)
        repo_lifecycle_df = build_repo_lifecycle_df(omo_df)
        if len(repo_lifecycle_df):
            print(f"    [REPO-LIFECYCLE] {len(repo_lifecycle_df)} daily records, "
                  f"peak cumulative: {repo_lifecycle_df['repo_cumulative'].max():,.0f} 亿元", flush=True)
        chinabond_df = build_chinabond_df(args.start_date, args.end_date, verbose=True,
                                           files=missing_chinabond_files)

        # After reading SHIBOR/China bond, check for additional dates not in
        # the instruments CSV (e.g., trading days with SHIBOR data but no OMO).
        # --date mode pins the scope to the forced date — no extra dates.
        if forced is None:
            for df in [shibor_df, chinabond_df]:
                if df is not None and len(df) > 0:
                    extra_dates = set(
                        dates_as_date_list(_host(df)["date"])) - existing_dates_set
                    if extra_dates:
                        missing_dates = missing_dates | extra_dates
                        print(f"    → Found {len(extra_dates)} additional missing dates "
                              f"from SHIBOR/China bond (not in instruments CSV)", flush=True)

        # ------------------------------------------------------------------
        # (5) Filter to missing dates and insert
        # ------------------------------------------------------------------
        print("\n[5/5] Inserting data to database (missing dates only) …", flush=True)

        # Insert new identities for missing dates only
        identity_rows = [{"date": d} for d in sorted(missing_dates)]
        await insert_rows(conn, "stats.debt_identity", identity_rows, ["date"])

        # Helper: filter a frame to missing dates, convert to rows for insert.
        # Frames stay cudf-dispatched through processing; the host unwrap
        # happens ONCE here before numpy filtering, and records_from_frame
        # emits datetime.date + NaN→None at the DB boundary (no .dt.date
        # object-date column, no to_dict("records") per-row extraction).
        def df_to_missing_rows(df, date_col="date"):
            if df is None or len(df) == 0:
                return []
            df = _host(df)
            missing_d64 = np.asarray(sorted(missing_dates), dtype="datetime64[D]")
            d = np.asarray(df[date_col]).astype("datetime64[D]")
            df = df.loc[np.isin(d, missing_d64)]
            if len(df) == 0:
                return []
            return records_from_frame(df, np.asarray(df.columns).tolist())

        # Insert each source table, filtered to missing dates
        table_source_pairs = [
            ("stats.debt_omo",            omo_df),
            ("stats.debt_repo",           repo_lifecycle_df),
            ("stats.debt_outright_repo",  outright_df),
            ("stats.debt_mlf",            mlf_df),
            ("stats.debt_lpr",            lpr_df),
            ("stats.debt_shibor",         shibor_df),
            ("stats.debt_treasury",       chinabond_df),
        ]
        for tbl, df in table_source_pairs:
            rows = df_to_missing_rows(df)
            if rows:
                await insert_rows(conn, tbl, rows, ["date"],
                                  suffix=f" (filtered to {len(missing_dates)} missing dates)")
            else:
                print(f"    [DB] No new rows to insert into {tbl}", flush=True)

    finally:
        await conn.close()

    # Coverage summary (over full source range, not just missing)
    print(f"\n  Coverage by source (full range):", flush=True)
    if oma_df is not None and len(oma_df) > 0:
        print(f"    · PBoC OMA            : {len(oma_df):>5d} announcements", flush=True)
    if omo_df is not None and len(omo_df) > 0:
        n = int(omo_df["omo_rate"].notna().sum())
        print(f"    · PBoC OMO rate       : {n:>5d} days", flush=True)
    if outright_df is not None and len(outright_df) > 0:
        n = int((outright_df["outright_repo_marker"] == 1).sum())
        print(f"    · PBoC outright-repo  : {n:>5d} announcements", flush=True)
    if mlf_df is not None and len(mlf_df) > 0:
        n = int((mlf_df["mlf_marker"] == 1).sum())
        print(f"    · PBoC MLF            : {n:>5d} announcements", flush=True)
    if lpr_df is not None and len(lpr_df) > 0:
        n = int(lpr_df["lpr_1y"].notna().sum())
        print(f"    · PBoC LPR (1Y)       : {n:>5d} announcements", flush=True)
    if shibor_df is not None and len(shibor_df) > 0:
        n = int(shibor_df["shibor_o_n"].notna().sum())
        print(f"    · SHIBOR O/N          : {n:>5d} days", flush=True)
    if chinabond_df is not None and len(chinabond_df) > 0:
        n = int(chinabond_df["cb_1y"].notna().sum())
        print(f"    · China bond 1Y yield  : {n:>5d} days", flush=True)

    print_wall_time(t0)
