"""
build_classification — Build two-level (sector → industry) security
classification for ETF + Index + Stock, persist INDEX classification to
sec_classification.json, and upsert all to stats.sec_classification +
stats.sec_index_tags.

Classification model (hierarchy reflected by the package layout under
builds/classification/sector_industry/):

  1. INDICES (sector_industry/index/) — classification (sector, industry) is
     loaded directly from sec_classification.json (the authoritative,
     hand-editable cache).  New indices not yet in the JSON are classified by
     keyword rules and added to the JSON on the next save.
     parent_index_code = '' (empty string, root of hierarchy).
  2. ETFs (sector_industry/index/etf/) — always recomputed from CSV + index
     inheritance (one-to-one ETF → tracking index).  When the parent index is
     unclassified (OTHER) or missing, a name-based fallback applies the same
     keyword rules to the ETF name.  'IB names' (foreign-branded ETFs lacking
     the standard Chinese suffix) are classified by their underlying
     index_name instead.  parent_index_code = tracking index code.
  3. STOCKS (sector_industry/index/stock/) — always recomputed from DB
     sec_composition.  ONE ROW PER qualifying index (weight > 2%, excluding
     BROAD-sector indices).  A stock may therefore have multiple rows.
     parent_index_code = each qualifying index code, parent_index_weight.
     Stocks without any qualifying index → single row with
     parent_index_code = '' and (OTHER, OTHER).

The JSON contains ONLY the catalog + index classifications (no ETF/stock
data).  ETF and stock mappings are rebuilt every run and upserted to the
DB directly — they are never persisted to the JSON.

Usage:
  python -m builds.classification                 # load JSON indices + recompute ETFs/stocks + save JSON + upsert DB
  python -m builds.classification --no-db         # same but skip DB upsert
  python -m builds.classification --force         # truncate sec_classification before upsert (removes stale rows)
  python -m builds.classification --reclassify    # reclassify ALL indices from keyword rules (ignores JSON cache)
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys

from _common.build_commons import (
    setup_utf8_stdout, get_db_or_exit,
    print_build_header,
    TODAY_STR,
    add_force_arg,
)

setup_utf8_stdout()

from builds.classification.sector_industry.paths import CSV_DIR, JSON_PATH
from builds.classification.sector_industry.csv_loader import (
    find_latest_csv, load_etf_index_csv)
from builds.classification.sector_industry.json_io import load_json, save_json
from builds.classification.sector_industry.owners import load_owners
from builds.classification.sector_industry.build import build_classification
from builds.classification.sector_industry.upsert import upsert_to_db


async def main():
    ap = argparse.ArgumentParser(
        description="Build two-level security classification (sector → industry)."
    )
    ap.add_argument("--no-db", action="store_true",
                    help="Skip DB upsert (load JSON + recompute ETFs/stocks + save JSON only)")
    ap.add_argument("--reclassify", action="store_true",
                    help="Force reclassification of ALL indices from keyword rules "
                         "(ignores stale JSON-cached sector_id/industry_id/tags). "
                         "Use this after changing INDEX_RULES to propagate new rules.")
    add_force_arg(ap)
    args = ap.parse_args()

    t0 = datetime.datetime.now()
    mode_parts = []
    if args.no_db:
        mode_parts.append("no-db")
    if args.force:
        mode_parts.append("FORCE (truncate + recompute)")
    else:
        mode_parts.append("incremental (upsert)")
    if args.reclassify:
        mode_parts.append("reclassify")
    print_build_header(
        "BUILD CLASSIFICATION  ·  sector → industry  ·  ETF + Index + Stock",
        **{
            "JSON path": JSON_PATH,
            "Today": TODAY_STR,
            "Mode": " + ".join(mode_parts),
        }
    )

    # --- Load JSON (index classifications — the authoritative source) ---
    prev_state = load_json()
    if prev_state:
        print(f"    [JSON] Loaded index classifications: "
              f"{len(prev_state.get('indices', {}))} indices", flush=True)
    else:
        print(f"    [JSON] No existing JSON — all indices will be classified by keyword rules",
              flush=True)

    # --- Load owners (sec_owners.json — curated ETF manager / company registry) ---
    owners = load_owners()

    # --- Load CSV (ETF → index mapping) ---
    csv_path = find_latest_csv()
    if csv_path is None:
        print(f"    [FATAL] No etf_index_map_*.csv found in {CSV_DIR}", flush=True)
        sys.exit(1)
    print(f"    [CSV] Loading ETF → index mapping: {os.path.basename(csv_path)}", flush=True)
    etf_rows = load_etf_index_csv(csv_path)
    print(f"    [CSV] {len(etf_rows)} ETF → index mappings loaded", flush=True)

    # --- Connect to DB (for index meta, stock mapping, and upsert) ---
    conn = None
    if not args.no_db:
        print("\n[1/2] Connecting to database …", flush=True)
        conn = await get_db_or_exit()

    try:
        print("\n[2/2] Building classification …", flush=True)
        state = await build_classification(
            conn, etf_rows, prev_state=prev_state, owners=owners, verbose=True,
            reclassify_indices=args.reclassify)

        # --- Save JSON (indices only) ---
        save_json(state)

        # --- Summary ---
        print(f"\n    Summary:", flush=True)
        print(f"      Catalog           : {len(state['catalog'])} sectors "
              f"(industry + strategy unified)", flush=True)
        print(f"      Indices           : {len(state.get('indices', {}))}", flush=True)
        print(f"      ETFs              : {len(state.get('etfs', {}))}", flush=True)
        print(f"      Stocks            : {len(state.get('stocks', []))} rows "
              f"({len(set(s['code'] for s in state.get('stocks', [])))} codes)", flush=True)
        print(f"      Owners            : {len(state.get('owners', []))}", flush=True)

        # --- Upsert to DB ---
        if conn is not None:
            print("\n[DB] Upserting to database …", flush=True)
            await upsert_to_db(conn, state, verbose=True, force=args.force)
    finally:
        if conn is not None:
            await conn.close()

    elapsed = (datetime.datetime.now() - t0).total_seconds()
    print(f"\n  Wall time: {elapsed:.1f}s", flush=True)
    print("=" * 78, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
