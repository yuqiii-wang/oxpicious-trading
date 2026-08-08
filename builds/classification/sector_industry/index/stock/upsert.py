"""DB upsert for the stock leaf.

Upserts stocks (type='stock') into stats.sec_classification.  Stocks can
have MULTIPLE rows (one per qualifying index). Delete all existing stock
rows first to avoid stale entries when the set of qualifying indexes
changes between runs.
"""
from __future__ import annotations

from typing import Any, Dict, List

from _common.build_commons import bulk_upsert_async

from builds.classification.sector_industry.catalog import _lookup_labels, _parse_date


async def upsert_stocks(
    conn,
    catalog: Dict[str, Any],
    stocks: List[Dict[str, Any]],
    verbose: bool = True,
) -> None:
    """Upsert stocks (type='stock') into stats.sec_classification."""
    # Stocks can have MULTIPLE rows (one per qualifying index). Delete all
    # existing stock rows first to avoid stale entries when the set of
    # qualifying indexes changes between runs.
    await conn.execute(
        "DELETE FROM stats.sec_classification WHERE type = 'stock'")

    stock_rows: List[Dict[str, Any]] = []
    for v in stocks:
        fd = v.get("first_date")
        ld = v.get("last_date")
        is_ind = v.get("is_industry_not_strategy", True)
        sector_label, industry_label, industry_slug = _lookup_labels(
            catalog, v["sector_id"], v["industry_id"])
        stock_rows.append({
            "code": v["code"],
            "name": v["name"],
            "type": "stock",
            "exchange": v["exchange"],
            "sector_id": v["sector_id"],
            "sector_label": sector_label,
            "industry_id": v["industry_id"],
            "industry_label": industry_label,
            "industry_slug": industry_slug,
            "is_industry_not_strategy": is_ind,
            "n_days": v.get("n_days", 0),
            "first_date": _parse_date(fd),
            "last_date": _parse_date(ld),
            "parent_index_code": v["parent_index_code"],
            "parent_index_weight": v["parent_index_weight"],
            "parent_index_is_primary": v.get("parent_index_is_primary", False),
            "owner_id": v.get("owner_id"),
            "is_dummy": False,
        })
    if stock_rows:
        inserted = await bulk_upsert_async(
            conn, "stats.sec_classification", stock_rows,
            ["code", "parent_index_code"])
        if verbose:
            print(f"    [DB] Upserted {inserted:,} stock rows into "
                  f"stats.sec_classification", flush=True)
