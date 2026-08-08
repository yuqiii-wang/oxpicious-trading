"""DB upsert for the ETF leaf.

Upserts ETFs (type='etf') into stats.sec_classification.  One-to-one ETF →
tracking index; PK is (code, parent_index_code).  ETF broad-market status
is derived on demand via the parent_index_code → sec_index_tags JOIN (no
denormalized column on sec_classification).
"""
from __future__ import annotations

from typing import Any, Dict, List

from _common.build_commons import bulk_upsert_async

from builds.classification.sector_industry.catalog import _lookup_labels


async def upsert_etfs(
    conn,
    catalog: Dict[str, Any],
    etfs: Dict[str, Any],
    verbose: bool = True,
) -> None:
    """Upsert ETFs (type='etf') into stats.sec_classification."""
    etf_rows: List[Dict[str, Any]] = []
    for code, v in etfs.items():
        is_ind = v.get("is_industry_not_strategy", True)
        sector_label, industry_label, industry_slug = _lookup_labels(
            catalog, v["sector_id"], v["industry_id"])
        etf_rows.append({
            "code": code,
            "name": v["name"],
            "type": "etf",
            "exchange": v["exchange"],
            "sector_id": v["sector_id"],
            "sector_label": sector_label,
            "industry_id": v["industry_id"],
            "industry_label": industry_label,
            "industry_slug": industry_slug,
            "is_industry_not_strategy": is_ind,
            "parent_index_code": v["parent_index_code"],
            "parent_index_weight": None,
            "parent_index_is_primary": v.get("parent_index_is_primary", False),
            "aum_yi": v.get("aum_yi"),
            "owner_id": v.get("owner_id"),
            "is_dummy": False,
        })
    if etf_rows:
        inserted = await bulk_upsert_async(
            conn, "stats.sec_classification", etf_rows,
            ["code", "parent_index_code"])
        if verbose:
            print(f"    [DB] Upserted {inserted:,} ETF rows into "
                  f"stats.sec_classification", flush=True)
