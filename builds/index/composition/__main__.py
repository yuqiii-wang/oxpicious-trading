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

With --date YYYY-MM-DD: single-snapshot-date rebuild — only composition
CSVs whose snapshot date matches are read and the missing-pair skip is
bypassed, so rows already in the DB are refreshed through the upsert
path (no DELETE, no truncation). Mutually exclusive with --force.

Usage:
  python -m builds.index.composition
  python -m builds.index.composition --force
  python -m builds.index.composition --date 2026-08-14   (force one snapshot date)
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
    enforce_date_force_exclusion, parse_date_arg, forced_date_scope,
    print_build_header, print_wall_time,
    PROJECT_ROOT, TODAY_STR,
)

setup_utf8_stdout()

import asyncio

from builds._commons.paths import INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR
from builds.index.composition import (
    build_index_composition_rows,
    build_szse_index_composition_rows,
    available_snapshot_dates,
)

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("composition")


async def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Build CSI + SZSE index composition and insert to stats.sec_composition (missing-data-only)."
    )
    add_common_build_args(ap)
    args = ap.parse_args()

    # --date mode: mutual exclusion + parse (SystemExit 2 on bad input),
    # then validate the forced snapshot date against the composition CSV
    # filenames (CSI + SZSE union) before any DB work. exits(1) when the
    # date has no source snapshot.
    enforce_date_force_exclusion(args)
    forced = parse_date_arg(args.date)
    if forced is not None:
        logger.info(f"[DATE MODE] Forced single-date build: {forced}")
        forced_date_scope(
            available_snapshot_dates(),
            forced,
            source_label="composition CSV filenames",
        )

    t0 = time.time()
    print_build_header(
        "BUILD INDEX COMPOSITION (CSI + SZSE)  ·  missing-data-only → stats.sec_composition",
        **{
            "CSI comp dir":  INDEX_COMP_DIR,
            "SZSE comp dir": SZSE_INDEX_COMP_DIR,
            "Forced date":   str(forced) if forced else "(none)",
            "Today":         TODAY_STR,
        }
    )

    # ------------------------------------------------------------------
    # (1) Connect to DB and find existing (code, snapshot_date) pairs
    # ------------------------------------------------------------------
    logger.info("\n[1/4] Connecting to database and detecting missing snapshots …")
    conn = await get_db_or_exit()

    try:
        if args.force:
            logger.info("    [DB] Force mode: deleting existing index composition rows "
                  "(source_type='index', ETF rows preserved)")
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
            logger.info(f"    [DB] {len(existing_comp_keys):,} existing (code, snapshot_date) pairs "
                  f"in stats.sec_composition (source_type='index')")

        # ------------------------------------------------------------------
        # (2) Build composition rows from CSI + SZSE CSVs
        # ------------------------------------------------------------------
        logger.info("\n[2/4] Building CSI index composition rows …")
        index_comp_rows = await build_index_composition_rows(
            conn=conn, force=args.force, forced_date=forced)

        logger.info("\n[3/4] Building SZSE index composition rows …")
        szse_index_comp_rows = await build_szse_index_composition_rows(
            conn=conn, force=args.force, forced_date=forced)

        all_rows = index_comp_rows + szse_index_comp_rows
        logger.info(f"\n    → total: {len(all_rows):,} index composition rows "
              f"({len(index_comp_rows):,} CSI + {len(szse_index_comp_rows):,} SZSE)")

        # ------------------------------------------------------------------
        # (3) Filter to missing (code, snapshot_date) pairs and insert
        # ------------------------------------------------------------------
        logger.info("\n[4/4] Filtering to missing pairs and inserting …")
        # --date mode bypasses the missing-pair filter: every row built from
        # the forced snapshot date is (re)written via the upsert path.
        if not args.force and existing_comp_keys and not forced:
            n_before = len(all_rows)
            all_rows = [
                r for r in all_rows
                if (r["code"], r["snapshot_date"]) not in existing_comp_keys
            ]
            n_skipped = n_before - len(all_rows)
            logger.info(f"    [DB] {len(all_rows):,} rows to insert "
                  f"(skipped {n_skipped:,} existing)")
        else:
            logger.info(f"    [DB] {len(all_rows):,} rows to insert")

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
            logger.info(f"    [DB] Inserted {total:,} rows into stats.sec_composition via {via}")
        else:
            logger.info(f"    [DB] No new rows to insert into stats.sec_composition")

    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
