"""
build_index_classification.py — Populate L1/L2 classification columns in stats.index_meta.

Reads index codes + names from stats.index_identity, classifies each index
via the unified taxonomy in _classification.py (classify_index_full →
sector + industry), and writes the result into stats.index_meta's
sector_id / sector_label / industry_id / industry_label / industry_slug
columns.

Mirrors build_etf_classification.py — the DB columns are the single source
of truth consumed by the TypeScript backend (index-baseline.service.ts).

This is a meta-only build (no date dimension): it derives L1/L2 labels from
each index's name and upserts them keyed by code. No CSV I/O. Every run
re-classifies all indices because name-based taxonomy may change when
_classification.py is updated.

Usage:
  python build_index_classification.py
  python build_index_classification.py --force   (truncate + rebuild all)
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _build_commons import (
    setup_utf8_stdout, add_common_build_args,
    get_db_connection_async, bulk_upsert_async, truncate_table_async,
    print_build_header, print_wall_time,
)
from _classification import classify_index_full

setup_utf8_stdout()

import asyncio
import argparse
from collections import Counter


async def main():
    ap = argparse.ArgumentParser()
    add_common_build_args(ap)
    args = ap.parse_args()

    t0 = time.time()
    print_build_header(
        "BUILD INDEX CLASSIFICATION (L1 sector + L2 industry → stats.index_meta)",
    )

    conn = await get_db_connection_async()
    try:
        if args.force:
            print("\n[DB] Force mode: truncating stats.index_meta …", flush=True)
            await truncate_table_async(conn, "stats.index_meta")

        # 1. Read all distinct indices from index_identity with coverage stats
        print("\n[1/3] Reading indices from stats.index_identity …", flush=True)
        rows = await conn.fetch(
            """
            SELECT code,
                   MAX(name) AS name,
                   COUNT(*)   AS n_days,
                   MIN(date)  AS first_date,
                   MAX(date)  AS last_date
              FROM stats.index_identity
             GROUP BY code
             ORDER BY n_days DESC, code
            """
        )
        print(f"    → {len(rows):,} indices", flush=True)

        # 2. Classify each index
        print("\n[2/3] Classifying indices via _classification.classify_index_full() …", flush=True)
        update_rows = []
        sector_counter = Counter()
        industry_counter = Counter()
        for r in rows:
            code = r["code"]
            name = r["name"] or ""
            sector_id, sector_label, industry_id, industry_label, industry_slug = classify_index_full(name, code)
            update_rows.append({
                "code": code,
                "name": name,
                "n_days": r["n_days"],
                "first_date": r["first_date"],
                "last_date": r["last_date"],
                "sector_id": sector_id,
                "sector_label": sector_label,
                "industry_id": industry_id,
                "industry_label": industry_label,
                "industry_slug": industry_slug,
            })
            sector_counter[sector_id] += 1
            industry_counter[industry_id] += 1

        n_classified = sum(1 for r in update_rows if r["sector_id"] not in ("OTHER", "BROAD"))
        print(f"    → {n_classified:,}/{len(update_rows):,} indices classified into a specific sector", flush=True)
        print(f"    → {len(update_rows) - n_classified:,} indices in BROAD/OTHER", flush=True)

        print(f"\n    Sector distribution (L1):", flush=True)
        for sid, cnt in sector_counter.most_common():
            label = next((r["sector_label"] for r in update_rows if r["sector_id"] == sid), sid)
            print(f"      {sid:16s}  {label:10s}  {cnt:>4} indices", flush=True)

        print(f"\n    Top 20 industries (L2):", flush=True)
        for iid, cnt in industry_counter.most_common(20):
            label = next((r["industry_label"] for r in update_rows if r["industry_id"] == iid), iid)
            print(f"      {iid:20s}  {label[:30]:30s}  {cnt:>4} indices", flush=True)

        # 3. Upsert classification into index_meta
        print(f"\n[3/3] Upserting classification into stats.index_meta …", flush=True)
        inserted = await bulk_upsert_async(
            conn, "stats.index_meta", update_rows, ["code"]
        )
        print(f"    [DB] Updated {inserted:,} rows in stats.index_meta", flush=True)

    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
