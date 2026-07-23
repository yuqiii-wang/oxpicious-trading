"""
build_stock_industry.py — Build stock → industry mapping table.

Derives the stock→industry classification from EXISTING database data:
  1. Reads ETF names from stats.etf_meta (and stats.etf_identity)
  2. Classifies each ETF via the unified taxonomy in _classification.py
     (only industry-specific ETFs — theme_id starts with "IND_" — count;
     broad/index themes like 创业板/港股/宽基 return None)
  3. Joins with stats.sec_composition to assign each stock the industry
  of the most frequently-appearing industry-specific ETF that holds it
  4. Populates stats.stock_industry_map with industry + L1 sector info
     (sector_id, sector_label, industry_id derived from
     _classification.classify_stock())

No external network access required — uses only data already in the database.

Usage:
  python build_stock_industry.py
  python build_stock_industry.py --force   # truncate before insert
"""
import os, sys, time, argparse
import warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _db_commons import get_db_connection_async, bulk_upsert_async, truncate_table_async
# Unified classification taxonomy (single source of truth).
from _classification import classify_industry_from_name, classify_stock

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
from collections import Counter, defaultdict


def classify_etf_name(name: str) -> str | None:
    """Extract the industry from an ETF/LOF name.

    Returns the clean industry label (e.g. "半导体", "医药", "光伏"), or
    None for broad/index ETFs that don't map to a single industry.

    Uses _classification.classify_industry_from_name() which searches
    INDUSTRIES keywords directly (bypassing ETF_THEMES).  This means an ETF
    like "创业板新能源ETF" — primarily a GEM theme — still extracts
    "新能源" as the industry, because the stocks it holds are 新能源 stocks.
    """
    if not name:
        return None
    _sid, _slab, _iid, ind_label = classify_industry_from_name(name)
    if ind_label and ind_label != "未分类":
        return ind_label
    return None  # broad/index ETF or no industry keyword matched


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="truncate table before insert")
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 70)
    print("  BUILD STOCK INDUSTRY MAP (from existing DB data)")
    print("=" * 70)

    conn = await get_db_connection_async()
    try:
        # 1. Read ETF names from etf_meta + etf_identity
        print("\n[1/4] Reading ETF names from stats.etf_meta + etf_identity …", flush=True)
        etf_rows = await conn.fetch(
            """
            SELECT code, MAX(name) AS name
              FROM (
                SELECT code, name FROM stats.etf_meta
                UNION
                SELECT code, name FROM stats.etf_identity
              ) sub
             GROUP BY code
            """
        )
        print(f"    → {len(etf_rows)} unique ETFs", flush=True)

        # 2. Classify each ETF
        print("\n[2/4] Classifying ETFs by industry keyword …", flush=True)
        etf_industry: dict[str, str] = {}
        unclassified_examples = []
        for r in etf_rows:
            code = r["code"]
            name = r["name"] or ""
            industry = classify_etf_name(name)
            if industry:
                etf_industry[code] = industry
            else:
                if len(unclassified_examples) < 20:
                    unclassified_examples.append((code, name))

        n_classified = len(etf_industry)
        n_total = len(etf_rows)
        print(f"    → {n_classified}/{n_total} ETFs classified into an industry", flush=True)
        print(f"    → {n_total - n_classified} ETFs are broad/index (unclassified)", flush=True)

        # Show industry distribution
        ind_counter = Counter(etf_industry.values())
        print(f"\n    Industry distribution (top 30):", flush=True)
        for ind, cnt in ind_counter.most_common(30):
            print(f"      {ind:15s}  {cnt:>4} ETFs", flush=True)

        if unclassified_examples:
            print(f"\n    Sample unclassified ETF names:", flush=True)
            for code, name in unclassified_examples[:15]:
                print(f"      {code}  {name}", flush=True)

        # 3. Read sec_composition (ETF holdings only) and assign industries to stocks
        print("\n[3/4] Assigning industries to stocks via sec_composition …", flush=True)
        holding_rows = await conn.fetch(
            """
            SELECT DISTINCT code, stock_code, MAX(stock_name) AS stock_name
              FROM stats.sec_composition
             WHERE source_type = 'etf'
             GROUP BY code, stock_code
            """
        )
        print(f"    → {len(holding_rows):,} unique (ETF, stock) pairs", flush=True)

        # For each stock, tally industries of ETFs that hold it
        stock_industry_votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        stock_names: dict[str, str] = {}
        n_matched = 0
        for r in holding_rows:
            etf_code = r["code"]
            stock_code = str(r["stock_code"]).strip().zfill(6)
            stock_name = r["stock_name"] or ""
            if stock_name and stock_code not in stock_names:
                stock_names[stock_code] = stock_name

            # Try both full code (e.g. "159001.SZ") and stripped code ("159001")
            etf_stripped = etf_code.split(".")[0] if "." in etf_code else etf_code
            industry = etf_industry.get(etf_code) or etf_industry.get(etf_stripped)
            if industry:
                stock_industry_votes[stock_code][industry] += 1
                n_matched += 1

        print(f"    → {n_matched:,} pairs matched to an industry ETF", flush=True)
        print(f"    → {len(stock_industry_votes):,} stocks got at least one industry vote", flush=True)

        # Assign the majority-vote industry to each stock
        all_stock_codes = set()
        for r in holding_rows:
            all_stock_codes.add(str(r["stock_code"]).strip().zfill(6))

        result_rows = []
        n_assigned = 0
        n_sector_matched = 0
        for stock_code in sorted(all_stock_codes):
            votes = stock_industry_votes.get(stock_code, {})
            if votes:
                # Pick the industry with the most votes (ETF count)
                best_industry = max(votes, key=votes.get)
            else:
                best_industry = "未分类"
            # Derive L1 sector + L2 industry id from the industry label via
            # the unified _classification taxonomy.
            sector_id, sector_label, industry_id, _ = classify_stock(best_industry)
            if sector_id != "OTHER":
                n_sector_matched += 1
            result_rows.append({
                "stock_code": stock_code,
                "stock_name": stock_names.get(stock_code, ""),
                "industry": best_industry,
                "industry_code": None,
                "sector_id": sector_id,
                "sector_label": sector_label,
                "industry_id": industry_id,
            })
            if best_industry != "未分类":
                n_assigned += 1

        print(f"\n    → {n_assigned:,}/{len(result_rows):,} stocks assigned a specific industry", flush=True)
        print(f"    → {n_sector_matched:,}/{len(result_rows):,} stocks mapped to an L1 sector", flush=True)
        print(f"    → {len(result_rows) - n_assigned:,} stocks marked as '未分类'", flush=True)

        # Show sample results
        print(f"\n    Sample assignments:", flush=True)
        for r in result_rows[:10]:
            print(f"      {r['stock_code']}  {r['stock_name']:15s}  → {r['industry']}", flush=True)

        # 4. Insert into database
        print(f"\n[4/4] Inserting into stats.stock_industry_map …", flush=True)
        if args.force:
            await truncate_table_async(conn, "stats.stock_industry_map")
        inserted = await bulk_upsert_async(
            conn, "stats.stock_industry_map", result_rows, ["stock_code"]
        )
        print(f"    [DB] Inserted {inserted:,} rows into stats.stock_industry_map", flush=True)

    finally:
        await conn.close()

    print(f"\n  Wall time: {int(time.time()-t0)}s")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
