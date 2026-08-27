"""
builds.index.composition — Build CSI + SZSE index composition snapshots
and insert directly to stats.sec_composition (missing-data-only, no
intermediate CSV).

Reads the per-index closeweight CSVs produced by download scripts:
  • CSI:  temps/csi_index_composition/*_closeweight_*.csv
  • SZSE: temps/szse_index_composition/*_closeweight_*.csv

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

Usage:
  python -m builds.index.composition
  python -m builds.index.composition --force
"""

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import time

from _common.build_commons import (
    setup_utf8_stdout, add_common_build_args, get_db_or_exit,
    copy_or_upsert_split_async,
    print_build_header, print_wall_time,
    PROJECT_ROOT, TODAY_STR,
)

setup_utf8_stdout()

import asyncio

from builds._commons.paths import INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR
from builds.index.composition import (
    build_index_composition_rows,
    build_szse_index_composition_rows,
)


async def main():
    import argparse

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
        index_comp_rows = await build_index_composition_rows(conn=conn, force=args.force)

        print("\n[3/4] Building SZSE index composition rows …", flush=True)
        szse_index_comp_rows = await build_szse_index_composition_rows(conn=conn, force=args.force)

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
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, "stats.sec_composition", all_rows,
                ["code", "snapshot_date", "rank"],
                date_column="snapshot_date",
            )
            total = n_copied + n_upserted
            via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                  f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
                  "upsert"
            print(f"    [DB] Inserted {total:,} rows into stats.sec_composition via {via}", flush=True)
        else:
            print(f"    [DB] No new rows to insert into stats.sec_composition", flush=True)

    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
