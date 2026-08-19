"""builds.index — Unified index build orchestrator.

Runs index composition first (CSI + SZSE), then index baseline (CSIndex daily
OHLCV + PE + MAs). Composition must run before baseline because baseline's
close-price estimation relies on index composition shared weights.

Usage:
  python -m builds.index
  python -m builds.index --force
  python -m builds.index --start-date 2024-01-01 --end-date 2026-07-23
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

from _common.build_commons import (
    setup_utf8_stdout,
    add_common_build_args,
    print_build_header,
    print_wall_time,
    TODAY_STR,
)

setup_utf8_stdout()

from builds.index.composition import (
    build_index_composition_rows,
    build_szse_index_composition_rows,
)
from builds._commons.paths import INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR


async def _run_composition(force: bool) -> None:
    """Run index composition build (CSI + SZSE) → stats.sec_composition."""
    from _common.build_commons import get_db_or_exit, copy_or_upsert_split_async

    print("\n    Building CSI index composition rows …", flush=True)
    index_comp_rows = build_index_composition_rows(verbose=True)

    print("\n    Building SZSE index composition rows …", flush=True)
    szse_index_comp_rows = build_szse_index_composition_rows(verbose=True)

    all_rows = index_comp_rows + szse_index_comp_rows
    print(f"\n    → total: {len(all_rows):,} index composition rows "
          f"({len(index_comp_rows):,} CSI + {len(szse_index_comp_rows):,} SZSE)", flush=True)

    conn = await get_db_or_exit()
    try:
        if force:
            print("    [DB] Force mode: deleting existing index composition rows", flush=True)
            await conn.execute(
                "DELETE FROM stats.sec_composition WHERE source_type = 'index'"
            )
            existing_comp_keys: set = set()
        else:
            comp_existing_rows = await conn.fetch(
                "SELECT DISTINCT code, snapshot_date "
                "FROM stats.sec_composition WHERE source_type = 'index'"
            )
            existing_comp_keys = {
                (r["code"], r["snapshot_date"]) for r in comp_existing_rows
            }
            print(f"    [DB] {len(existing_comp_keys):,} existing (code, snapshot_date) pairs", flush=True)

        if not force and existing_comp_keys:
            n_before = len(all_rows)
            all_rows = [
                r for r in all_rows
                if (r["code"], r["snapshot_date"]) not in existing_comp_keys
            ]
            n_skipped = n_before - len(all_rows)
            print(f"    [DB] {len(all_rows):,} rows to insert (skipped {n_skipped:,} existing)", flush=True)

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
            print("    [DB] No new rows to insert", flush=True)
    finally:
        await conn.close()


async def main():
    ap = argparse.ArgumentParser(
        description="Build index composition (CSI+SZSE) then baseline (CSIndex daily)."
    )
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD INDEX  ·  composition (CSI+SZSE) → baseline (CSIndex daily)",
        **{
            "CSI comp dir":  INDEX_COMP_DIR,
            "SZSE comp dir": SZSE_INDEX_COMP_DIR,
            "Date range":    f"{args.start_date or '(all)'} → {args.end_date or '(all)'}",
            "Today":         TODAY_STR,
        }
    )

    # ---- Phase 1: Index composition (CSI + SZSE) ----------------------
    print("\n" + "=" * 78)
    print("  PHASE 1: INDEX COMPOSITION (CSI + SZSE)")
    print("=" * 78)
    t1 = time.time()
    await _run_composition(force=args.force)
    print(f"\n  Composition phase done ({int(time.time() - t1)}s)", flush=True)

    # ---- Phase 2: Index baseline (CSIndex daily) ----------------------
    print("\n" + "=" * 78)
    print("  PHASE 2: INDEX BASELINE (CSIndex daily OHLCV + PE + MAs)")
    print("=" * 78)
    t2 = time.time()

    # baseline.main uses sys.argv for argparse; set it and call directly.
    # --refresh-estimated-days: nightly self-heal — rebuild recent daily
    # rows gap-filled as ESTIMATED by a build that raced ahead of the
    # EOD CSV publish; idempotent when the CSVs already agree.
    original_argv = sys.argv.copy()
    try:
        sys.argv = [
            "builds.index.baseline",
            *(["--start-date", args.start_date] if args.start_date else []),
            *(["--end-date", args.end_date] if args.end_date else []),
            *(["--force"] if args.force else []),
            *([] if args.force else ["--refresh-estimated-days", "10"]),
        ]
        from builds.index.baseline import main as baseline_main
        await baseline_main()
    finally:
        sys.argv = original_argv

    print(f"\n  Baseline phase done ({int(time.time() - t2)}s)", flush=True)
    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
