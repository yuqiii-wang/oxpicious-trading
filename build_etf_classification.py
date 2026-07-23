"""
build_etf_classification.py — Populate L1/L2 classification columns in stats.etf_meta.

Reads ETF names from stats.etf_meta, classifies each ETF via the unified
taxonomy in _classification.py (classify_etf_full → sector + industry),
and writes the result back into stats.etf_meta's sector_id / sector_label /
industry_id / industry_label / industry_slug columns.

The DB columns are the single source of truth consumed by the TypeScript
backend (etf-margin.service.ts) — no classification logic is duplicated in TS.

Usage:
  python build_etf_classification.py
"""
import os, sys, time
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_commons import get_db_connection_async, bulk_upsert_async
from _classification import classify_etf_full, get_industry_index

# stdout encoding (Windows)
import locale as _locale
try:
    _locale.setlocale(_locale.LC_ALL, "")
except Exception:
    pass
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import asyncio
from collections import Counter


async def main():
    t0 = time.time()
    print("=" * 70)
    print("  BUILD ETF CLASSIFICATION (L1 sector + L2 industry → stats.etf_meta)")
    print("=" * 70)

    conn = await get_db_connection_async()
    try:
        # 1. Read all ETFs from etf_meta (code is stored WITHOUT exchange suffix)
        print("\n[1/3] Reading ETFs from stats.etf_meta …", flush=True)
        rows = await conn.fetch(
            """
            SELECT code, name
              FROM stats.etf_meta
             ORDER BY data_quality_score DESC, code
            """
        )
        print(f"    → {len(rows):,} ETFs", flush=True)

        # 2. Classify each ETF
        print("\n[2/3] Classifying ETFs via _classification.classify_etf_full() …", flush=True)
        update_rows = []
        sector_counter = Counter()
        industry_counter = Counter()
        index_count = 0
        for r in rows:
            code = r["code"]
            name = r["name"] or ""
            sector_id, sector_label, industry_id, industry_label, industry_slug = classify_etf_full(name)
            # Primary tracking index for this ETF's industry (used as a
            # composition fallback in the UI when the ETF has no holdings).
            idx_code, idx_name = get_industry_index(industry_id)
            if idx_code:
                index_count += 1
            update_rows.append({
                "code": code,
                "sector_id": sector_id,
                "sector_label": sector_label,
                "industry_id": industry_id,
                "industry_label": industry_label,
                "industry_slug": industry_slug,
                "index_code": idx_code or "",
                "index_name": idx_name or "",
            })
            sector_counter[sector_id] += 1
            industry_counter[industry_id] += 1

        n_classified = sum(1 for r in update_rows if r["sector_id"] != "OTHER")
        print(f"    → {n_classified:,}/{len(update_rows):,} ETFs classified into a sector", flush=True)
        print(f"    → {len(update_rows) - n_classified:,} ETFs unclassified (OTHER)", flush=True)
        print(f"    → {index_count:,} ETFs have a tracking index (composition fallback)", flush=True)

        print(f"\n    Sector distribution (L1):", flush=True)
        for sid, cnt in sector_counter.most_common():
            label = next((r["sector_label"] for r in update_rows if r["sector_id"] == sid), sid)
            print(f"      {sid:8s}  {label:10s}  {cnt:>4} ETFs", flush=True)

        print(f"\n    Top 20 industries (L2):", flush=True)
        for iid, cnt in industry_counter.most_common(20):
            label = next((r["industry_label"] for r in update_rows if r["industry_id"] == iid), iid)
            print(f"      {iid:20s}  {label[:30]:30s}  {cnt:>4} ETFs", flush=True)

        # 3. Upsert classification columns back into etf_meta
        print(f"\n[3/3] Upserting classification into stats.etf_meta …", flush=True)
        inserted = await bulk_upsert_async(
            conn, "stats.etf_meta", update_rows, ["code"]
        )
        print(f"    [DB] Updated {inserted:,} rows in stats.etf_meta", flush=True)

    finally:
        await conn.close()

    print(f"\n  Wall time: {int(time.time()-t0)}s")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
