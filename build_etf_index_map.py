"""
build_etf_index_map.py — Build ETF → index mapping table.

Derives the ETF→index mapping from EXISTING database data:
  1. Reads ETF names from stats.etf_meta
  2. Classifies each ETF via the unified taxonomy in _classification.py
  3. Gets the primary tracking index for each ETF's industry via get_industry_index()
  4. Populates stats.etf_index_map with etf_code, etf_name, index_code, index_name,
     and L1/L2 classification (sector_id, sector_label, industry_id, industry_label)

No external network access required — uses only data already in the database.
No CSV I/O. No date-gap detection — every run rebuilds the full mapping
because the underlying classification taxonomy may have changed.

Usage:
  python build_etf_index_map.py
  python build_etf_index_map.py --force   # truncate before insert
"""
import os, sys, time, argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _build_commons import (
    setup_utf8_stdout, get_db_connection_async, bulk_upsert_async,
    truncate_table_async, print_build_header, print_wall_time,
)
# Unified classification taxonomy (single source of truth).
from _classification import classify_etf_full, get_industry_index

setup_utf8_stdout()

import asyncio
from collections import Counter


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="truncate table before insert")
    args = parser.parse_args()

    t0 = time.time()
    print_build_header("BUILD ETF INDEX MAP (from existing DB data)")

    conn = await get_db_connection_async()
    try:
        # 1. Read ETF names from etf_meta (code is stored WITH exchange suffix)
        print("\n[1/3] Reading ETFs from stats.etf_meta …", flush=True)
        etf_rows = await conn.fetch(
            """
            SELECT code, name
              FROM stats.etf_meta
             ORDER BY data_quality_score DESC, code
            """
        )
        print(f"    → {len(etf_rows):,} ETFs", flush=True)

        # 2. Classify each ETF and get its tracking index
        print("\n[2/3] Classifying ETFs and deriving tracking indices …", flush=True)
        result_rows = []
        sector_counter = Counter()
        industry_counter = Counter()
        index_count = 0
        for r in etf_rows:
            etf_code = r["code"]
            etf_name = r["name"] or ""
            # Classify the ETF to get L1/L2 taxonomy
            sector_id, sector_label, industry_id, industry_label, _ = classify_etf_full(etf_name)
            # Get the primary tracking index for this ETF's industry
            idx_code, idx_name = get_industry_index(industry_id)
            if idx_code:
                index_count += 1
            result_rows.append({
                "etf_code": etf_code,
                "etf_name": etf_name,
                "index_code": idx_code or "",
                "index_name": idx_name or "",
                "sector_id": sector_id,
                "sector_label": sector_label,
                "industry_id": industry_id,
                "industry_label": industry_label,
            })
            sector_counter[sector_id] += 1
            industry_counter[industry_id] += 1

        n_classified = sum(1 for r in result_rows if r["sector_id"] != "OTHER")
        print(f"    → {n_classified:,}/{len(result_rows):,} ETFs classified into a sector", flush=True)
        print(f"    → {len(result_rows) - n_classified:,} ETFs unclassified (OTHER)", flush=True)
        print(f"    → {index_count:,} ETFs have a tracking index", flush=True)

        print(f"\n    Sector distribution (L1):", flush=True)
        for sid, cnt in sector_counter.most_common():
            label = next((r["sector_label"] for r in result_rows if r["sector_id"] == sid), sid)
            print(f"      {sid:8s}  {label:10s}  {cnt:>4} ETFs", flush=True)

        print(f"\n    Top 20 industries (L2):", flush=True)
        for iid, cnt in industry_counter.most_common(20):
            label = next((r["industry_label"] for r in result_rows if r["industry_id"] == iid), iid)
            print(f"      {iid:20s}  {label[:30]:30s}  {cnt:>4} ETFs", flush=True)

        # Show sample results
        print(f"\n    Sample ETF → index mappings:", flush=True)
        for r in result_rows[:10]:
            idx_info = f"→ {r['index_code']} {r['index_name']}" if r["index_code"] else "→ no index"
            print(f"      {r['etf_code']}  {r['etf_name'][:20]:20s}  {idx_info}", flush=True)

        # 3. Insert into database
        print(f"\n[3/3] Inserting into stats.etf_index_map …", flush=True)
        if args.force:
            await truncate_table_async(conn, "stats.etf_index_map")
        inserted = await bulk_upsert_async(
            conn, "stats.etf_index_map", result_rows, ["etf_code"]
        )
        print(f"    [DB] Inserted {inserted:,} rows into stats.etf_index_map", flush=True)

    finally:
        await conn.close()

    print_wall_time(t0)


if __name__ == "__main__":
    asyncio.run(main())
