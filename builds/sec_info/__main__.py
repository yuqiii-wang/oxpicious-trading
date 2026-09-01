"""builds.sec_info — Build SZSE ETF quarterly report registry + owner registry.

Loads three DB targets from the SZSE ETF quarterly report CSVs
(temps/szse_etf_reports/<code>/<code>_<YYYYQn>_*.csv) plus the curated
sec_owners.json:

  1. stats.sec_owners  — truncate + rebuild from _common/sec_statics/sec_owners.json
                         (migrated here from builds.classification).
  2. stats.sec_info    — one row per fund code, static attributes from the
                         LATEST identify.csv (latest-value snapshot).
  3. stats.sec_reports — one row per (code, report quarter): report header +
                         asset-allocation MIX (equity / fixed income / cash /
                         derivatives / ...) + section content flags.
  4. stats.sec_composition — top10_holdings.csv injection (source_type='etf',
                         snapshot_date = report quarter-end). Skips snapshots
                         already loaded by builds.etf so full-composition data
                         is never overwritten by the smaller top-10 source.

Missing-data pattern (DB-first):
  · sec_info    — query existing {code: last_report_date}; only write codes
                  whose latest report_date is newer (or new codes).
  · sec_reports — query existing (code, report_date) pairs; skip present.
  · sec_composition — query existing (code, snapshot_date) pairs; skip present.
  · sec_owners  — always truncate + rebuild (keeps JSON in sync).

With --force: truncate sec_info + sec_reports + sec_owners first, then load
all.  sec_composition is NEVER force-cleared here (only missing snapshots are
added — builds.etf owns the ETF composition truncation).

With --date YYYY-MM-DD: single-report-quarter rebuild — every parsed
collection is restricted to that quarter-end report date, and the sec_info /
sec_reports missing-data skips are bypassed (rows are refreshed through the
normal upsert paths; no truncation, no deletes).  The sec_owners
truncate+rebuild is skipped (date-independent registry — run without --date
to refresh it).  sec_composition keeps its missing-snapshot guard in every
mode: the top-10 source never overwrites snapshots already loaded by
builds.etf.  Mutually exclusive with --force.

Usage:
  python -m builds.sec_info              # incremental (missing data only)
  python -m builds.sec_info --force      # truncate sec_info + sec_reports + sec_owners, reload all
  python -m builds.sec_info --date 2025-12-31  # force one report quarter-end (upsert refresh, no truncate)
  python -m builds.sec_info --no-owners  # skip sec_owners rebuild
  python -m builds.sec_info --no-composition  # skip top10 → sec_composition injection
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
from typing import Any, Dict, List

from _common.build_commons import (
    setup_utf8_stdout, get_db_or_exit, print_build_header,
    add_force_arg, add_date_arg, parse_date_arg,
    enforce_date_force_exclusion, forced_date_scope, TODAY_STR,
)

setup_utf8_stdout()

from builds.sec_info.paths import SZSE_REPORTS_DIR
from builds.sec_info.loaders import (
    iter_report_files, load_identify, load_asset_portfolio, load_top10_holdings,
)
from builds.sec_info.upsert import (
    upsert_owners, fetch_existing_sec_info, build_sec_info_rows, upsert_sec_info,
    fetch_existing_sec_reports, build_sec_reports_rows, upsert_sec_reports,
    fetch_existing_composition_keys, build_composition_rows, inject_top10_composition,
)
from builds.classification.sector_industry.owners import load_owners


# Default MIX columns (NULL when asset_portfolio.csv is empty / absent).
_MIX_COLS = [
    "equity_amt", "equity_pct", "fixed_income_amt", "fixed_income_pct",
    "precious_metal_amt", "precious_metal_pct", "derivatives_amt", "derivatives_pct",
    "reverse_repo_amt", "reverse_repo_pct", "bank_deposit_amt", "bank_deposit_pct",
    "other_assets_amt", "other_assets_pct", "total_assets_amt", "total_assets_pct",
]
# Full sec_reports column list (order matches the table DDL).
_REPORT_COLS = [
    "code", "report_period", "report_year", "report_quarter", "report_date",
    "total_shares", "total_shares_text",
] + _MIX_COLS + [
    "has_asset_portfolio", "has_industry_portfolio", "has_top10_holdings",
    "has_bond_type_portfolio", "has_top10_bonds", "has_remaining_maturity",
    "n_asset_portfolio_rows", "n_industry_portfolio_rows", "n_top10_holdings_rows",
    "n_bond_type_portfolio_rows", "n_top10_bonds_rows", "n_remaining_maturity_rows",
]


def _parse_owners_from_json() -> List[Dict[str, Any]]:
    """Load sec_owners.json → list of owner dicts (empty on failure)."""
    # load_owners prints its own [OWNERS] line; reuse it for consistency.
    return load_owners()


def _gather_reports(reports_dir: str) -> tuple:
    """Scan + parse all report CSVs.

    Returns (latest_per_code, report_rows, top10_snapshots):
      latest_per_code   — {code: identify_dict} keeping the NEWEST report per code
                          (for sec_info statics).
      report_rows       — list of full sec_reports row dicts (header + MIX +
                          content flags), one per (code, quarter).
      top10_snapshots   — list of {etf_code, snapshot_date, holdings} for
                          sec_composition injection (only reports with
                          has_top10_holdings + a non-empty top10_holdings.csv).
    """
    files = iter_report_files(reports_dir)
    # Group file paths by (code, year, quarter) → {type: path}.
    by_key: Dict[tuple, Dict[str, str]] = {}
    for code, year, quarter, ftype, path in files:
        by_key.setdefault((code, year, quarter), {})[ftype] = path

    latest_per_code: Dict[str, Dict[str, Any]] = {}
    report_rows: List[Dict[str, Any]] = []
    top10_snapshots: List[Dict[str, Any]] = []

    for (code, year, quarter), ftypes in sorted(by_key.items()):
        id_path = ftypes.get("identify")
        if id_path is None:
            continue
        info = load_identify(id_path)
        if info is None:
            continue
        report_date = info["report_date"]

        # Track the latest identify per code for sec_info statics.
        prev = latest_per_code.get(code)
        if prev is None or report_date > prev["report_date"]:
            latest_per_code[code] = info

        # Build the sec_reports row: header + content flags from identify, then
        # overlay the asset-allocation MIX from asset_portfolio.csv.
        row: Dict[str, Any] = {c: info.get(c) for c in _REPORT_COLS}
        ap_path = ftypes.get("asset_portfolio")
        if ap_path:
            mix = load_asset_portfolio(ap_path)
            for c in _MIX_COLS:
                row[c] = mix.get(c)
        report_rows.append(row)

        # top10_holdings → sec_composition snapshot (only when has_top10_holdings
        # AND the CSV has content).
        if info.get("has_top10_holdings"):
            t10_path = ftypes.get("top10_holdings")
            if t10_path:
                holdings = load_top10_holdings(t10_path)
                if holdings:
                    top10_snapshots.append({
                        "etf_code": f"{code}.SZ",
                        "snapshot_date": report_date,
                        "holdings": holdings,
                    })

    return latest_per_code, report_rows, top10_snapshots


async def main():
    ap = argparse.ArgumentParser(
        description="Build SZSE ETF report registry (sec_info + sec_reports + sec_owners)."
    )
    ap.add_argument("--no-owners", action="store_true",
                    help="Skip sec_owners rebuild (keep existing table)")
    ap.add_argument("--no-composition", action="store_true",
                    help="Skip top10_holdings → sec_composition injection")
    add_force_arg(ap)
    add_date_arg(ap)
    args = ap.parse_args()

    # --date mode: mutual exclusion + parse (SystemExit 2 on bad input).
    # The forced quarter-end is validated against the parsed report dates
    # after the CSV scan, before any DB work (forced_date_scope exits(1)).
    enforce_date_force_exclusion(args)
    forced = parse_date_arg(args.date)
    if forced is not None:
        print(f"[DATE MODE] Forced single-date build: {forced}", flush=True)

    t0 = datetime.datetime.now()
    mode = ("FORCE (truncate + reload)" if args.force else
            f"DATE MODE (single-date refresh: {forced})" if forced is not None else
            "incremental (missing data)")
    print_build_header(
        "BUILD SEC INFO  ·  SZSE ETF reports + owner registry",
        **{
            "Reports dir": SZSE_REPORTS_DIR,
            "Today": TODAY_STR,
            "Mode": mode,
        }
    )

    if not os.path.isdir(SZSE_REPORTS_DIR):
        print(f"    [FATAL] Reports dir not found: {SZSE_REPORTS_DIR}", flush=True)
        sys.exit(1)

    # --- 1. Scan + parse all report CSVs ---
    print("\n[1/4] Scanning + parsing report CSVs …", flush=True)
    latest_per_code, report_rows, top10_snapshots = _gather_reports(SZSE_REPORTS_DIR)
    n_codes = len(latest_per_code)
    n_reports = len(report_rows)
    n_top10 = len(top10_snapshots)
    n_top10_rows = sum(len(s["holdings"]) for s in top10_snapshots)
    print(f"    [CSV] {n_codes} funds, {n_reports} report quarters, "
          f"{n_top10} top10 snapshots ({n_top10_rows} holdings rows)", flush=True)

    # --- 1b. --date scope: restrict every parsed collection to the forced
    #     quarter-end report date (validated before any DB work) ---
    if forced is not None:
        available_dates = {r["report_date"] for r in report_rows}
        target_dates = forced_date_scope(
            available_dates, forced,
            source_label="SZSE ETF report CSVs (quarter-end report dates)")
        latest_per_code = {c: i for c, i in latest_per_code.items()
                           if i["report_date"] in target_dates}
        report_rows = [r for r in report_rows
                       if r["report_date"] in target_dates]
        top10_snapshots = [s for s in top10_snapshots
                           if s["snapshot_date"] in target_dates]
        n_codes = len(latest_per_code)
        n_reports = len(report_rows)
        n_top10 = len(top10_snapshots)
        n_top10_rows = sum(len(s["holdings"]) for s in top10_snapshots)
        print(f"    [DATE MODE] Restricted to {forced}: {n_codes} funds, "
              f"{n_reports} report quarters, {n_top10} top10 snapshots "
              f"({n_top10_rows} holdings rows)", flush=True)

    # --- 2. Connect to DB ---
    print("\n[2/4] Connecting to database …", flush=True)
    conn = await get_db_or_exit()

    try:
        # --- 3. sec_owners (truncate + rebuild) ---
        if args.no_owners:
            print("\n[3/4] sec_owners: --no-owners, skipping", flush=True)
        elif forced is not None:
            print(f"\n[3/4] sec_owners: DATE MODE {forced}, skipping "
                  f"(date-independent registry rebuild — run without --date "
                  f"to refresh)", flush=True)
        else:
            print("\n[3/4] Rebuilding stats.sec_owners …", flush=True)
            owners = _parse_owners_from_json()
            await upsert_owners(conn, owners, verbose=True)

        # --- 4. sec_info (latest snapshot per code, missing-data) ---
        print("\n[4/4] Upserting stats.sec_info + sec_reports + sec_composition …",
              flush=True)
        # --date mode bypasses the missing-data skips: the skip filters see
        # "nothing existing", so every parsed row of the forced date is
        # re-written through the normal upsert path (force stays False →
        # no truncation, no deletes).
        bypass = forced is not None
        if bypass:
            print("    [DB] DATE MODE: sec_info/sec_reports missing-data "
                  "skips bypassed — forced-date rows re-upserted", flush=True)
        existing_info = {} if bypass else await fetch_existing_sec_info(conn)
        info_rows = build_sec_info_rows(latest_per_code, existing_info, args.force)
        await upsert_sec_info(conn, info_rows, args.force)

        # --- sec_reports (missing-data unless --force / --date) ---
        existing_reports = set() if bypass else await fetch_existing_sec_reports(conn)
        report_rows_to_write = build_sec_reports_rows(report_rows, existing_reports, args.force)
        await upsert_sec_reports(conn, report_rows_to_write, args.force)

        # --- sec_composition top10 injection (always missing-data) ---
        if args.no_composition:
            print("    [DB] --no-composition: skipping top10 → sec_composition",
                  flush=True)
        else:
            existing_comp = await fetch_existing_composition_keys(conn)
            if forced is not None:
                print("    [DB] DATE MODE: sec_composition keeps its "
                      "missing-snapshot guard (builds.etf full snapshots are "
                      "never overwritten by the top-10 source)", flush=True)
            comp_rows = build_composition_rows(top10_snapshots, existing_comp)
            await inject_top10_composition(conn, comp_rows)

        # --- Summary ---
        print(f"\n    Summary:", flush=True)
        print(f"      Funds (sec_info)     : {len(info_rows):,} upserted "
              f"({n_codes} parsed)", flush=True)
        print(f"      Reports (sec_reports): {len(report_rows_to_write):,} upserted "
              f"({n_reports} parsed)", flush=True)
        if not args.no_composition:
            print(f"      top10 → sec_composition: {len(comp_rows):,} rows from "
                  f"{n_top10} snapshots", flush=True)
    finally:
        await conn.close()

    elapsed = (datetime.datetime.now() - t0).total_seconds()
    print(f"\n  Wall time: {elapsed:.1f}s", flush=True)
    print("=" * 78, flush=True)


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
