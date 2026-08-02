"""
build_index_composition.py — Build CSI + SZSE index composition snapshots
and insert directly to stats.sec_composition (missing-data-only, no
intermediate CSV).

Reads the per-index closeweight CSVs produced by download scripts:
  • CSI:  temps/csi_index_composition/*_closeweight_*.csv
           (produced by download_csindex_linked_etf.py +
            download_index_composition.py — CSI indices like 000300,
            000905, 000852, etc., with weights from CSI)
  • SZSE: temps/szse_index_composition/*_closeweight_*.csv
           (produced by download_szse_index_composition.py — SZSE indices
            like 399001 深证成指, 399006 创业板指, 399237 运输指数, etc.,
            with weights computed from float shares)

Each CSV contains one snapshot_date for one index_code with columns
(snapshot_date, index_code, stock_code, stock_name, weight_pct). Rows
are mapped to stats.sec_composition with source_type='index', ranked by
weight descending within each (code, snapshot_date) group.

Missing-data detection flow (DB-first):
  1. Query stats.sec_composition for existing (code, snapshot_date) pairs
     where source_type='index'
  2. Read all composition CSVs into rows
  3. Filter to missing (code, snapshot_date) pairs
  4. Bulk upsert only the missing rows

With --force: DELETE FROM stats.sec_composition WHERE source_type='index'
first (ETF composition rows are preserved — they are owned by
builds.etf). Then read ALL source CSVs and insert.

NOTE: stats.sec_composition is shared between ETF composition (source_type='etf',
loaded by builds.etf) and index composition (source_type='index', loaded here).
This script only touches index rows.

Usage:
  python -m builds.index.composition
  python -m builds.index.composition --force
"""
import os, glob, time, argparse
import datetime

import warnings
warnings.filterwarnings("ignore")

import pandas as pd

from utils.build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    bulk_upsert_async,
    print_build_header, print_wall_time,
    PROJECT_ROOT, TODAY_STR,
)

setup_utf8_stdout()

import asyncio

# ============================================================================
# Paths
# ============================================================================
INDEX_COMP_DIR      = os.path.join(PROJECT_ROOT, "temps", "csi_index_composition")
SZSE_INDEX_COMP_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_index_composition")


# ============================================================================
# CSI index composition: read closeweight CSVs
# ============================================================================
def build_index_composition_rows(verbose=True):
    """Read CSI index composition CSVs and build rows for stats.sec_composition.

    Returns a list of dicts with keys:
      snapshot_date, code, source_type, rank, stock_code, stock_name, weight_pct
    """
    if not os.path.isdir(INDEX_COMP_DIR):
        if verbose:
            print(f"    [INDEX-COMP] dir not found: {INDEX_COMP_DIR}", flush=True)
        return []

    files = sorted(glob.glob(os.path.join(INDEX_COMP_DIR, "*_closeweight_*.csv")))
    if not files:
        if verbose:
            print(f"    [INDEX-COMP] no CSVs found in {INDEX_COMP_DIR}", flush=True)
        return []

    if verbose:
        print(f"    [INDEX-COMP] {len(files)} CSV files in {INDEX_COMP_DIR}", flush=True)

    dfs = []
    for path in files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        dfs.append(df)

    if not dfs:
        return []

    combined = pd.concat(dfs, ignore_index=True)
    for c in ("snapshot_date", "index_code", "stock_code", "stock_name", "weight_pct"):
        if c not in combined.columns:
            if verbose:
                print(f"    [INDEX-COMP] WARN: missing column '{c}'", flush=True)
            return []
    combined["weight_pct"] = pd.to_numeric(combined["weight_pct"], errors="coerce").fillna(0.0)
    combined = combined.sort_values(
        ["index_code", "snapshot_date", "weight_pct"],
        ascending=[True, True, False],
    ).reset_index(drop=True)

    rows = []
    for (index_code, snap_date), sub in combined.groupby(["index_code", "snapshot_date"]):
        snap_date_str = str(snap_date).strip()
        try:
            snap_date_obj = datetime.datetime.strptime(snap_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        for rank_idx, (_, r) in enumerate(sub.iterrows(), start=1):
            sc = str(r.get("stock_code", "")).strip()
            sc_stripped = sc.split(".")[0].zfill(6)
            if len(sc_stripped) != 6 or not sc_stripped.isdigit():
                continue
            rows.append({
                "snapshot_date": snap_date_obj,
                "code": str(index_code).strip().zfill(6),
                "source_type": "index",
                "rank": rank_idx,
                "stock_code": sc,
                "stock_name": str(r.get("stock_name", "") or ""),
                "weight_pct": float(r["weight_pct"]),
            })

    if verbose:
        n_indices = combined["index_code"].nunique()
        n_dates = combined["snapshot_date"].nunique()
        print(f"    [INDEX-COMP] {len(rows):,} rows from {n_indices} indices, "
              f"{n_dates} snapshot dates", flush=True)
    return rows


# ============================================================================
# SZSE index composition: read SZSE index composition CSVs
# ============================================================================
def build_szse_index_composition_rows(verbose=True):
    """Read SZSE index composition CSVs and build rows for stats.sec_composition.

    Reads files from temps/szse_index_composition/ which are produced by
    download_szse_index_composition.py. These contain the latest constituent
    stocks for SZSE indices like 399001 (深证成指), 399006 (创业板指), and
    399237 (运输指数), with weights computed from float shares.

    Returns a list of dicts with keys:
      snapshot_date, code, source_type, rank, stock_code, stock_name, weight_pct
    """
    if not os.path.isdir(SZSE_INDEX_COMP_DIR):
        if verbose:
            print(f"    [SZSE-INDEX-COMP] dir not found: {SZSE_INDEX_COMP_DIR}", flush=True)
        return []

    files = sorted(glob.glob(os.path.join(SZSE_INDEX_COMP_DIR, "*_closeweight_*.csv")))
    if not files:
        if verbose:
            print(f"    [SZSE-INDEX-COMP] no CSVs found in {SZSE_INDEX_COMP_DIR}", flush=True)
        return []

    if verbose:
        print(f"    [SZSE-INDEX-COMP] {len(files)} CSV files in {SZSE_INDEX_COMP_DIR}", flush=True)

    dfs = []
    for path in files:
        try:
            df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        dfs.append(df)

    if not dfs:
        return []

    combined = pd.concat(dfs, ignore_index=True)
    for c in ("snapshot_date", "index_code", "stock_code", "stock_name", "weight_pct"):
        if c not in combined.columns:
            if verbose:
                print(f"    [SZSE-INDEX-COMP] WARN: missing column '{c}'", flush=True)
            return []
    combined["weight_pct"] = pd.to_numeric(combined["weight_pct"], errors="coerce").fillna(0.0)
    combined = combined.sort_values(
        ["index_code", "snapshot_date", "weight_pct"],
        ascending=[True, True, False],
    ).reset_index(drop=True)

    rows = []
    for (index_code, snap_date), sub in combined.groupby(["index_code", "snapshot_date"]):
        snap_date_str = str(snap_date).strip()
        try:
            snap_date_obj = datetime.datetime.strptime(snap_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        for rank_idx, (_, r) in enumerate(sub.iterrows(), start=1):
            sc = str(r.get("stock_code", "")).strip()
            sc_stripped = sc.split(".")[0].zfill(6)
            if len(sc_stripped) != 6 or not sc_stripped.isdigit():
                continue
            rows.append({
                "snapshot_date": snap_date_obj,
                "code": str(index_code).strip().zfill(6),
                "source_type": "index",
                "rank": rank_idx,
                "stock_code": sc,
                "stock_name": str(r.get("stock_name", "") or ""),
                "weight_pct": float(r["weight_pct"]),
            })

    if verbose:
        n_indices = combined["index_code"].nunique()
        n_dates = combined["snapshot_date"].nunique()
        print(f"    [SZSE-INDEX-COMP] {len(rows):,} rows from {n_indices} indices, "
              f"{n_dates} snapshot dates", flush=True)
    return rows


# ============================================================================
# Main pipeline
# ============================================================================
async def main():
    ap = argparse.ArgumentParser(
        description="Build CSI + SZSE index composition and insert to stats.sec_composition (missing-data-only)."
    )
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD INDEX COMPOSITION (CSI + SZSE)  ·  missing-data-only → stats.sec_composition",
        **{
            "CSI comp dir":  INDEX_COMP_DIR,
            "SZSE comp dir": SZSE_INDEX_COMP_DIR,
            "Today":         TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # (1) Connect to DB and find existing (code, snapshot_date) pairs
    # ------------------------------------------------------------------
    print("\n[1/4] Connecting to database and detecting missing snapshots …", flush=True)
    conn = await get_db_or_exit()

    try:
        if args.force:
            print("    [DB] Force mode: deleting existing index composition rows "
                  "(source_type='index', ETF rows preserved)", flush=True)
            await conn.execute(
                "DELETE FROM stats.sec_composition WHERE source_type = 'index'"
            )
            existing_comp_keys = set()
        else:
            comp_existing_rows = await conn.fetch(
                "SELECT DISTINCT code, snapshot_date "
                "FROM stats.sec_composition WHERE source_type = 'index'"
            )
            existing_comp_keys = {
                (r["code"], r["snapshot_date"]) for r in comp_existing_rows
            }
            print(f"    [DB] {len(existing_comp_keys):,} existing (code, snapshot_date) pairs "
                  f"in stats.sec_composition (source_type='index')", flush=True)

        # ------------------------------------------------------------------
        # (2) Build composition rows from CSI + SZSE CSVs
        # ------------------------------------------------------------------
        print("\n[2/4] Building CSI index composition rows …", flush=True)
        index_comp_rows = build_index_composition_rows(verbose=True)

        print("\n[3/4] Building SZSE index composition rows …", flush=True)
        szse_index_comp_rows = build_szse_index_composition_rows(verbose=True)

        all_rows = index_comp_rows + szse_index_comp_rows
        print(f"\n    → total: {len(all_rows):,} index composition rows "
              f"({len(index_comp_rows):,} CSI + {len(szse_index_comp_rows):,} SZSE)", flush=True)

        # ------------------------------------------------------------------
        # (3) Filter to missing (code, snapshot_date) pairs and insert
        # ------------------------------------------------------------------
        print("\n[4/4] Filtering to missing pairs and inserting …", flush=True)
        if not args.force and existing_comp_keys:
            n_before = len(all_rows)
            all_rows = [
                r for r in all_rows
                if (r["code"], r["snapshot_date"]) not in existing_comp_keys
            ]
            n_skipped = n_before - len(all_rows)
            print(f"    [DB] {len(all_rows):,} rows to insert "
                  f"(skipped {n_skipped:,} existing)", flush=True)
        else:
            print(f"    [DB] {len(all_rows):,} rows to insert", flush=True)

        if all_rows:
            inserted = await bulk_upsert_async(
                conn, "stats.sec_composition", all_rows,
                ["code", "snapshot_date", "rank"],
            )
            print(f"    [DB] Inserted {inserted:,} rows into stats.sec_composition", flush=True)
        else:
            print(f"    [DB] No new rows to insert into stats.sec_composition", flush=True)

    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
